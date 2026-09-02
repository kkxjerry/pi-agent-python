from __future__ import annotations

import asyncio
import copy
import inspect
import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from .cancellation import CancellationToken
from .errors import (
    ProviderErrorDetails,
    ProviderRequestError,
    provider_error_from_payload,
    transport_error,
)
from .event_stream import AssistantMessageEventStream
from .events import (
    DoneEvent,
    ErrorEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from .json_parse import parse_streaming_json
from .retry import parse_retry_after, retry_delay, sleep_with_cancellation
from .types import (
    AssistantMessage,
    Context,
    ImageContent,
    JsonValue,
    Message,
    Model,
    ProviderResponse,
    StopReason,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
    calculate_cost,
)

ResponseObserver = Callable[[ProviderResponse], Awaitable[None] | None]


@dataclass(slots=True)
class HttpStreamRequest:
    url: str
    headers: dict[str, str]
    body: bytes
    timeout: float
    signal: CancellationToken | None = None
    on_response: ResponseObserver | None = None


class AsyncLineTransport(Protocol):
    def stream_lines(self, request: HttpStreamRequest) -> AsyncIterator[str]: ...


class UrllibLineTransport:
    """Dependency-free HTTP transport with line-oriented streaming."""

    async def _open(self, request: HttpStreamRequest) -> Any:
        raw_request = urllib.request.Request(
            request.url,
            data=request.body,
            headers=request.headers,
            method="POST",
        )
        try:
            return await asyncio.to_thread(
                urllib.request.urlopen,
                raw_request,
                timeout=request.timeout,
            )
        except urllib.error.HTTPError as exc:
            body = await asyncio.to_thread(exc.read)
            text = body.decode("utf-8", errors="replace")
            try:
                payload: Any = json.loads(text)
            except json.JSONDecodeError:
                payload = {"message": text or f"HTTP {exc.code}"}
            headers = {str(key): str(value) for key, value in exc.headers.items()}
            raise provider_error_from_payload(
                payload,
                status_code=exc.code,
                headers=headers,
                retry_after=parse_retry_after(headers),
            ) from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise transport_error(f"Provider transport failed: {exc}", raw=str(exc)) from exc

    async def stream_lines(self, request: HttpStreamRequest) -> AsyncIterator[str]:
        response = await self._open(request)
        try:
            response_info = ProviderResponse(
                status=int(getattr(response, "status", 200)),
                headers={
                    str(key): str(value) for key, value in getattr(response, "headers", {}).items()
                },
            )
            if request.on_response is not None:
                observed = request.on_response(response_info)
                if inspect.isawaitable(observed):
                    await observed
            while True:
                if request.signal is not None:
                    request.signal.raise_if_cancelled()
                try:
                    raw = await asyncio.to_thread(response.readline)
                except (TimeoutError, OSError) as exc:
                    raise transport_error(
                        f"Provider stream read failed: {exc}",
                        raw=str(exc),
                    ) from exc
                if not raw:
                    return
                yield raw.decode("utf-8", errors="replace").rstrip("\r\n")
        finally:
            await asyncio.to_thread(response.close)


@dataclass(slots=True)
class _ToolAccumulator:
    content_index: int
    call: ToolCall
    raw_arguments: str = ""


class OpenAICompatibleProvider:
    """OpenAI Chat Completions-compatible SSE provider."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        transport: AsyncLineTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.transport = transport or UrllibLineTransport()

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        stream = AssistantMessageEventStream()
        task = asyncio.create_task(self._run(stream, model, context, options or StreamOptions()))
        stream.attach_task(task)
        return stream

    async def _run(
        self,
        stream: AssistantMessageEventStream,
        model: Model,
        context: Context,
        options: StreamOptions,
    ) -> None:
        attempt = 0
        try:
            while True:
                try:
                    await self._run_once(stream, model, context, options)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error = self._normalize_error(exc)
                    if (
                        not error.details.retryable
                        or error.details.response_started
                        or attempt >= max(options.max_retries, 0)
                    ):
                        stream.push(ErrorEvent("error", self._error_message(model, "error", error)))
                        return
                    try:
                        delay = retry_delay(
                            error,
                            attempt,
                            base=options.retry_base_delay,
                            maximum=options.max_retry_delay,
                        )
                    except ProviderRequestError as capped:
                        stream.push(
                            ErrorEvent("error", self._error_message(model, "error", capped))
                        )
                        return
                    await sleep_with_cancellation(delay, options.signal)
                    attempt += 1
        except asyncio.CancelledError as exc:
            stream.push(
                ErrorEvent(
                    "aborted",
                    self._error_message(
                        model,
                        "aborted",
                        str(exc) or "Operation aborted",
                    ),
                )
            )
        finally:
            stream.end()

    async def _run_once(
        self,
        stream: AssistantMessageEventStream,
        model: Model,
        context: Context,
        options: StreamOptions,
    ) -> None:
        partial = AssistantMessage(
            api=model.api,
            provider=model.provider,
            model=model.id,
            stop_reason="pending",
        )
        started = False
        text_index: int | None = None
        thinking_index: int | None = None
        tool_accumulators: dict[int, _ToolAccumulator] = {}
        finish_reason: str | None = None
        usage = Usage.zero()
        response_model: str | None = None
        response_id: str | None = None
        iterator: AsyncIterator[str] | None = None

        try:
            if options.signal is not None:
                options.signal.raise_if_cancelled()
            request = await self._build_request(model, context, options)
            iterator = self.transport.stream_lines(request).__aiter__()
            while True:
                try:
                    line = await self._next_line(iterator, options)
                except StopAsyncIteration:
                    break
                if options.signal is not None:
                    options.signal.raise_if_cancelled()
                payload = self._parse_sse_line(line)
                if payload is None:
                    continue
                if payload == "[DONE]":
                    break
                value = json.loads(payload)
                if not isinstance(value, dict):
                    raise ValueError("OpenAI stream payload must be an object")
                if value.get("error") is not None:
                    raise provider_error_from_payload(
                        value["error"],
                        response_started=started,
                    )
                response_model = self._string_or_none(value.get("model")) or response_model
                response_id = self._string_or_none(value.get("id")) or response_id
                usage_value = value.get("usage")
                if isinstance(usage_value, dict):
                    usage = self._parse_usage(usage_value)
                if not started:
                    partial.response_model = response_model
                    partial.response_id = response_id
                    stream.push(StartEvent(copy.deepcopy(partial)))
                    started = True

                choices = value.get("choices", [])
                if not isinstance(choices, list):
                    raise ValueError("OpenAI stream choices must be a list")
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    raw_finish = choice.get("finish_reason")
                    if isinstance(raw_finish, str):
                        finish_reason = raw_finish
                    delta = choice.get("delta", {})
                    if not isinstance(delta, dict):
                        continue
                    text_delta = delta.get("content")
                    if isinstance(text_delta, str) and text_delta:
                        if text_index is None:
                            text_index = len(partial.content)
                            partial.content.append(TextContent(""))
                            stream.push(TextStartEvent(text_index, copy.deepcopy(partial)))
                        text = cast(TextContent, partial.content[text_index])
                        text.text += text_delta
                        stream.push(TextDeltaEvent(text_index, text_delta, copy.deepcopy(partial)))

                    thinking_delta = delta.get("reasoning_content")
                    if thinking_delta is None:
                        thinking_delta = delta.get("reasoning")
                    if isinstance(thinking_delta, str) and thinking_delta:
                        if thinking_index is None:
                            thinking_index = len(partial.content)
                            partial.content.append(ThinkingContent(""))
                            stream.push(ThinkingStartEvent(thinking_index, copy.deepcopy(partial)))
                        thinking = cast(ThinkingContent, partial.content[thinking_index])
                        thinking.thinking += thinking_delta
                        stream.push(
                            ThinkingDeltaEvent(
                                thinking_index,
                                thinking_delta,
                                copy.deepcopy(partial),
                            )
                        )

                    raw_tool_calls = delta.get("tool_calls", [])
                    if isinstance(raw_tool_calls, list):
                        for raw_call in raw_tool_calls:
                            if not isinstance(raw_call, dict):
                                continue
                            provider_index = int(raw_call.get("index", 0))
                            accumulator = tool_accumulators.get(provider_index)
                            function = raw_call.get("function", {})
                            if not isinstance(function, dict):
                                function = {}
                            call_id = self._string_or_none(raw_call.get("id"))
                            name = self._string_or_none(function.get("name"))
                            if accumulator is None:
                                content_index = len(partial.content)
                                call = ToolCall(call_id or "", "", {})
                                partial.content.append(call)
                                accumulator = _ToolAccumulator(content_index, call)
                                tool_accumulators[provider_index] = accumulator
                                stream.push(
                                    ToolCallStartEvent(content_index, copy.deepcopy(partial))
                                )
                            if call_id:
                                accumulator.call.id = call_id
                            if name:
                                accumulator.call.name += name
                            argument_delta = function.get("arguments")
                            if isinstance(argument_delta, str) and argument_delta:
                                accumulator.raw_arguments += argument_delta
                                parsed = parse_streaming_json(accumulator.raw_arguments)
                                if isinstance(parsed, dict):
                                    accumulator.call.arguments = parsed
                                stream.push(
                                    ToolCallDeltaEvent(
                                        accumulator.content_index,
                                        argument_delta,
                                        copy.deepcopy(partial),
                                    )
                                )

            if not started:
                stream.push(StartEvent(copy.deepcopy(partial)))
                started = True
            if text_index is not None:
                text = cast(TextContent, partial.content[text_index])
                stream.push(TextEndEvent(text_index, text.text, copy.deepcopy(partial)))
            if thinking_index is not None:
                thinking = cast(ThinkingContent, partial.content[thinking_index])
                stream.push(
                    ThinkingEndEvent(
                        thinking_index,
                        thinking.thinking,
                        copy.deepcopy(partial),
                    )
                )
            for provider_index in sorted(tool_accumulators):
                accumulator = tool_accumulators[provider_index]
                accumulator.call.arguments = self._parse_tool_arguments(accumulator.raw_arguments)
                stream.push(
                    ToolCallEndEvent(
                        accumulator.content_index,
                        copy.deepcopy(accumulator.call),
                        copy.deepcopy(partial),
                    )
                )

            mapped_reason = self._map_finish_reason(finish_reason, bool(tool_accumulators))
            partial.stop_reason = mapped_reason
            partial.raw_stop_reason = finish_reason
            partial.response_model = response_model
            partial.response_id = response_id
            partial.usage = usage
            calculate_cost(model, usage)
            if options.signal is not None:
                options.signal.raise_if_cancelled()
            stream.push(DoneEvent(cast(Any, mapped_reason), copy.deepcopy(partial)))
        except asyncio.CancelledError:
            raise
        except ProviderRequestError as exc:
            raise exc.with_response_started(started) from exc
        except Exception as exc:
            raise ProviderRequestError(
                ProviderErrorDetails(
                    message=str(exc),
                    retryable=False,
                    raw=str(exc),
                    response_started=started,
                )
            ) from exc
        finally:
            if iterator is not None:
                close = getattr(iterator, "aclose", None)
                if callable(close):
                    closed = close()
                    if inspect.isawaitable(closed):
                        await closed

    async def _build_request(
        self,
        model: Model,
        context: Context,
        options: StreamOptions,
    ) -> HttpStreamRequest:
        base_url = (self.base_url or model.base_url).rstrip("/")
        if not base_url:
            raise ValueError("OpenAI-compatible base URL is required")
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"

        body: dict[str, Any] = {
            "model": model.id,
            "messages": self._convert_messages(context),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if context.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in context.tools
            ]
        if options.temperature is not None:
            body["temperature"] = options.temperature
        if options.max_tokens is not None:
            body["max_tokens"] = options.max_tokens
        if options.tool_choice is not None:
            body["tool_choice"] = options.tool_choice
        user_id = options.metadata.get("user_id")
        if isinstance(user_id, str):
            body["user"] = user_id
        if (
            options.session_id
            and options.cache_retention != "none"
            and model.compat.get("supportsPromptCacheKey") is True
        ):
            body["prompt_cache_key"] = options.session_id
            if options.cache_retention == "long":
                body["prompt_cache_retention"] = "24h"
        body.update(model.sampling_params)
        body.update(options.sampling_params)
        if options.on_payload is not None:
            replaced = options.on_payload(body, model)
            if inspect.isawaitable(replaced):
                replaced = await replaced
            if replaced is not None:
                if not isinstance(replaced, dict):
                    raise TypeError("on_payload must return a request object or None")
                body = replaced

        api_key = options.api_key or self.api_key
        defaults: dict[str, str | None] = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}" if api_key else None,
        }
        headers = self._merge_headers(defaults, model.headers, options.headers)

        async def observe_response(response: ProviderResponse) -> None:
            if options.on_response is None:
                return
            observed = options.on_response(response, model)
            if inspect.isawaitable(observed):
                await observed

        return HttpStreamRequest(
            url=url,
            headers=headers,
            body=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            timeout=options.timeout,
            signal=options.signal,
            on_response=observe_response if options.on_response is not None else None,
        )

    @staticmethod
    async def _next_line(iterator: AsyncIterator[str], options: StreamOptions) -> str:
        async def read_next() -> str:
            return await anext(iterator)

        line_task = asyncio.create_task(read_next())
        tasks: set[asyncio.Task[Any]] = {line_task}
        signal = options.signal
        cancel_task: asyncio.Task[None] | None = None
        if signal is not None:
            cancel_task = asyncio.create_task(signal.wait())
            tasks.add(cancel_task)
        idle_timeout = (
            options.stream_idle_timeout
            if options.stream_idle_timeout is not None
            else options.timeout
        )
        done, pending = await asyncio.wait(
            tasks,
            timeout=idle_timeout if idle_timeout > 0 else None,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise ProviderRequestError(
                ProviderErrorDetails(
                    message=f"Provider stream was idle for {idle_timeout:g}s",
                    retryable=True,
                )
            )
        if cancel_task is not None and cancel_task in done:
            line_task.cancel()
            await asyncio.gather(line_task, return_exceptions=True)
            assert signal is not None
            signal.raise_if_cancelled()
            raise asyncio.CancelledError("Operation aborted")
        if cancel_task is not None:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
        return line_task.result()

    @staticmethod
    def _parse_sse_line(line: str) -> str | None:
        stripped = line.strip()
        if not stripped or stripped.startswith(":"):
            return None
        if not stripped.startswith("data:"):
            return None
        return stripped[5:].lstrip()

    @staticmethod
    def _parse_tool_arguments(raw: str) -> dict[str, JsonValue]:
        value = parse_streaming_json(raw)
        if not isinstance(value, dict):
            raise ValueError("Tool call arguments must decode to an object")
        return value

    @staticmethod
    def _parse_usage(value: dict[str, Any]) -> Usage:
        prompt_details = value.get("prompt_tokens_details", {})
        completion_details = value.get("completion_tokens_details", {})
        if not isinstance(prompt_details, dict):
            prompt_details = {}
        if not isinstance(completion_details, dict):
            completion_details = {}
        cached = int(prompt_details.get("cached_tokens", 0) or 0)
        prompt_tokens = int(value.get("prompt_tokens", 0) or 0)
        output = int(value.get("completion_tokens", 0) or 0)
        return Usage(
            input=max(prompt_tokens - cached, 0),
            output=output,
            cache_read=cached,
            reasoning=int(completion_details.get("reasoning_tokens", 0) or 0),
            total_tokens=int(value.get("total_tokens", prompt_tokens + output) or 0),
        )

    @classmethod
    def _convert_messages(cls, context: Context) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if context.system_prompt:
            result.append({"role": "system", "content": context.system_prompt})
        for message in context.messages:
            result.extend(cls._convert_message(message))
        return result

    @classmethod
    def _convert_message(cls, message: Message) -> list[dict[str, Any]]:
        if isinstance(message, UserMessage):
            if isinstance(message.content, str):
                return [{"role": "user", "content": message.content}]
            content: list[dict[str, Any]] = []
            for user_block in message.content:
                if isinstance(user_block, TextContent):
                    content.append({"type": "text", "text": user_block.text})
                elif isinstance(user_block, ImageContent):
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (f"data:{user_block.mime_type};base64,{user_block.data}"),
                            },
                        }
                    )
            return [{"role": "user", "content": content}]
        if isinstance(message, ToolResultMessage):
            text = "".join(
                result_block.text
                for result_block in message.content
                if isinstance(result_block, TextContent)
            )
            return [
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "name": message.tool_name,
                    "content": text,
                }
            ]
        if not isinstance(message, AssistantMessage):
            raise TypeError(f"Unsupported model message: {type(message).__name__}")
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for assistant_block in message.content:
            if isinstance(assistant_block, TextContent):
                text_parts.append(assistant_block.text)
            elif isinstance(assistant_block, ThinkingContent):
                continue
            elif isinstance(assistant_block, ToolCall):
                tool_calls.append(
                    {
                        "id": assistant_block.id,
                        "type": "function",
                        "function": {
                            "name": assistant_block.name,
                            "arguments": json.dumps(
                                assistant_block.arguments,
                                separators=(",", ":"),
                            ),
                        },
                    }
                )
        value: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_parts) or None,
        }
        if tool_calls:
            value["tool_calls"] = tool_calls
        return [value]

    @staticmethod
    def _map_finish_reason(raw: str | None, has_tools: bool) -> StopReason:
        if raw in {"tool_calls", "function_call"} or (raw is None and has_tools):
            return "toolUse"
        if raw == "length":
            return "length"
        return "stop"

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _merge_headers(*sources: dict[str, str | None]) -> dict[str, str]:
        merged: dict[str, tuple[str, str]] = {}
        for source in sources:
            for name, value in source.items():
                key = name.lower()
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = (name, value)
        return {name: value for name, value in merged.values()}

    @staticmethod
    def _normalize_error(exc: Exception) -> ProviderRequestError:
        if isinstance(exc, ProviderRequestError):
            return exc
        return ProviderRequestError(
            ProviderErrorDetails(
                message=str(exc),
                retryable=False,
                raw=str(exc),
            )
        )

    @staticmethod
    def _error_message(
        model: Model,
        reason: StopReason,
        error: str | ProviderRequestError,
    ) -> AssistantMessage:
        diagnostics: list[dict[str, JsonValue]] = []
        if isinstance(error, ProviderRequestError):
            message = error.details.message
            diagnostics.append(error.details.diagnostic())
        else:
            message = error
        return AssistantMessage(
            api=model.api,
            provider=model.provider,
            model=model.id,
            stop_reason=reason,
            error_message=message,
            diagnostics=diagnostics,
        )
