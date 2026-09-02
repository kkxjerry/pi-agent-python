from __future__ import annotations

import asyncio
import copy
import json
from collections import deque
from dataclasses import dataclass
from typing import TypeAlias

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
    normal_stop_reason,
)
from .json_parse import parse_streaming_json
from .types import (
    AssistantMessage,
    Context,
    Model,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
)

FauxOutcome: TypeAlias = AssistantMessage | Exception


@dataclass(slots=True)
class FauxRequest:
    model: Model
    context: Context
    options: StreamOptions


class FauxProvider:
    """Deterministic provider used by parity and unit tests."""

    def __init__(self, responses: list[FauxOutcome] | None = None, *, chunk_size: int = 0) -> None:
        self._responses: deque[FauxOutcome] = deque(responses or [])
        self.chunk_size = chunk_size
        self.requests: list[FauxRequest] = []

    def enqueue(self, response: FauxOutcome) -> None:
        self._responses.append(response)

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        actual_options = options or StreamOptions()
        options_snapshot = StreamOptions(
            api_key=actual_options.api_key,
            temperature=actual_options.temperature,
            max_tokens=actual_options.max_tokens,
            signal=actual_options.signal,
            headers=dict(actual_options.headers),
            env=dict(actual_options.env),
            metadata=copy.deepcopy(actual_options.metadata),
            sampling_params=copy.deepcopy(actual_options.sampling_params),
            session_id=actual_options.session_id,
            tool_choice=actual_options.tool_choice,
            reasoning=actual_options.reasoning,
            transport=actual_options.transport,
            cache_retention=actual_options.cache_retention,
            timeout=actual_options.timeout,
            stream_idle_timeout=actual_options.stream_idle_timeout,
            max_retries=actual_options.max_retries,
            retry_base_delay=actual_options.retry_base_delay,
            max_retry_delay=actual_options.max_retry_delay,
            on_payload=actual_options.on_payload,
            on_response=actual_options.on_response,
        )
        self.requests.append(
            FauxRequest(copy.deepcopy(model), copy.deepcopy(context), options_snapshot)
        )
        stream = AssistantMessageEventStream()
        task = asyncio.create_task(self._run(stream, model, actual_options))
        stream.attach_task(task)
        return stream

    async def _run(
        self,
        stream: AssistantMessageEventStream,
        model: Model,
        options: StreamOptions,
    ) -> None:
        try:
            if options.signal is not None:
                options.signal.raise_if_cancelled()
            if not self._responses:
                raise RuntimeError("FauxProvider has no queued response")
            outcome = self._responses.popleft()
            if isinstance(outcome, Exception):
                raise outcome
            final = copy.deepcopy(outcome)
            final.api = final.api or model.api
            final.provider = final.provider or model.provider
            final.model = final.model or model.id
            partial = AssistantMessage(
                api=final.api,
                provider=final.provider,
                model=final.model,
                response_model=final.response_model,
                response_id=final.response_id,
                usage=Usage.zero(),
                stop_reason="pending",
                timestamp=final.timestamp,
            )
            stream.push(StartEvent(copy.deepcopy(partial)))
            for block in final.content:
                if options.signal is not None:
                    options.signal.raise_if_cancelled()
                index = len(partial.content)
                if isinstance(block, TextContent):
                    current = TextContent("")
                    partial.content.append(current)
                    stream.push(TextStartEvent(index, copy.deepcopy(partial)))
                    for chunk in self._chunks(block.text):
                        current.text += chunk
                        stream.push(TextDeltaEvent(index, chunk, copy.deepcopy(partial)))
                        await asyncio.sleep(0)
                    stream.push(TextEndEvent(index, current.text, copy.deepcopy(partial)))
                elif isinstance(block, ThinkingContent):
                    current_thinking = ThinkingContent("")
                    partial.content.append(current_thinking)
                    stream.push(ThinkingStartEvent(index, copy.deepcopy(partial)))
                    for chunk in self._chunks(block.thinking):
                        current_thinking.thinking += chunk
                        stream.push(ThinkingDeltaEvent(index, chunk, copy.deepcopy(partial)))
                        await asyncio.sleep(0)
                    current_thinking.thinking_signature = block.thinking_signature
                    current_thinking.redacted = block.redacted
                    stream.push(
                        ThinkingEndEvent(index, current_thinking.thinking, copy.deepcopy(partial))
                    )
                else:
                    current_call = ToolCall(block.id, block.name, {})
                    partial.content.append(current_call)
                    stream.push(ToolCallStartEvent(index, copy.deepcopy(partial)))
                    raw_arguments = json.dumps(
                        block.arguments, separators=(",", ":"), sort_keys=True
                    )
                    accumulated = ""
                    for chunk in self._chunks(raw_arguments):
                        accumulated += chunk
                        parsed = parse_streaming_json(accumulated)
                        if isinstance(parsed, dict):
                            current_call.arguments = parsed
                        stream.push(ToolCallDeltaEvent(index, chunk, copy.deepcopy(partial)))
                        await asyncio.sleep(0)
                    current_call.arguments = copy.deepcopy(block.arguments)
                    current_call.thought_signature = block.thought_signature
                    current_call.namespace = block.namespace
                    stream.push(
                        ToolCallEndEvent(index, copy.deepcopy(current_call), copy.deepcopy(partial))
                    )
            if options.signal is not None:
                options.signal.raise_if_cancelled()
            if final.stop_reason in {"error", "aborted"}:
                stream.push(ErrorEvent(final.stop_reason, final))
            else:
                stream.push(DoneEvent(normal_stop_reason(final.stop_reason), final))
        except asyncio.CancelledError as exc:
            message = self._error_message(model, "aborted", str(exc) or "Operation aborted")
            stream.push(ErrorEvent("aborted", message))
        except Exception as exc:
            message = self._error_message(model, "error", str(exc))
            stream.push(ErrorEvent("error", message))
        finally:
            stream.end()

    def _chunks(self, value: str) -> list[str]:
        if not value:
            return [""]
        if self.chunk_size <= 0:
            return [value]
        return [
            value[index : index + self.chunk_size]
            for index in range(0, len(value), self.chunk_size)
        ]

    @staticmethod
    def _error_message(model: Model, reason: str, message: str) -> AssistantMessage:
        return AssistantMessage(
            api=model.api,
            provider=model.provider,
            model=model.id,
            stop_reason=reason,  # type: ignore[arg-type]
            error_message=message,
        )
