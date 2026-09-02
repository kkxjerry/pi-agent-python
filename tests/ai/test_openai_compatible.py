from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest

from pi_agent.ai import (
    CancellationToken,
    Context,
    ErrorEvent,
    Model,
    OpenAICompatibleProvider,
    ProviderErrorDetails,
    ProviderRequestError,
    ProviderResponse,
    StreamOptions,
    TextContent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolDefinition,
    UserMessage,
)
from pi_agent.ai.openai_compatible import HttpStreamRequest


class FakeTransport:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.request: HttpStreamRequest | None = None

    async def stream_lines(self, request: HttpStreamRequest) -> AsyncIterator[str]:
        self.request = request
        if request.on_response is not None:
            observed = request.on_response(
                ProviderResponse(status=200, headers={"x-request-id": "fixture"})
            )
            if inspect.isawaitable(observed):
                await observed
        for line in self.lines:
            yield line


class ScriptedTransport:
    def __init__(self, outcomes: list[list[str] | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[HttpStreamRequest] = []

    async def stream_lines(self, request: HttpStreamRequest) -> AsyncIterator[str]:
        self.requests.append(request)
        if not self.outcomes:
            raise RuntimeError("transport script exhausted")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        for line in outcome:
            yield line


class IdleTransport:
    async def stream_lines(self, request: HttpStreamRequest) -> AsyncIterator[str]:
        del request
        await asyncio.Event().wait()
        yield "unreachable"


class FailsAfterFirstLineTransport:
    def __init__(self) -> None:
        self.requests: list[HttpStreamRequest] = []

    async def stream_lines(self, request: HttpStreamRequest) -> AsyncIterator[str]:
        self.requests.append(request)
        yield data({"choices": [{"delta": {"content": "partial"}}]})
        raise ProviderRequestError(
            ProviderErrorDetails(
                message="connection lost",
                retryable=True,
            )
        )


def data(value: object) -> str:
    return f"data: {json.dumps(value, separators=(',', ':'))}"


@pytest.mark.asyncio
async def test_openai_provider_streams_text_usage_and_serializes_request(model: Model) -> None:
    transport = FakeTransport(
        [
            data({"id": "r1", "model": "actual", "choices": [{"delta": {"content": "hel"}}]}),
            data({"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}),
            data(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "total_tokens": 14,
                        "prompt_tokens_details": {"cached_tokens": 3},
                    },
                }
            ),
            "data: [DONE]",
        ]
    )
    provider = OpenAICompatibleProvider(api_key="secret", transport=transport)
    context = Context(
        system_prompt="system",
        messages=[UserMessage("hello")],
        tools=[ToolDefinition("echo", "Echo", {"type": "object"})],
    )
    stream = provider.stream(model, context, StreamOptions(temperature=0.2, max_tokens=100))
    events = [event async for event in stream]
    result = await stream.result()

    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_delta",
        "text_end",
        "done",
    ]
    assert result.content == [TextContent("hello")]
    assert result.response_id == "r1"
    assert result.response_model == "actual"
    assert result.usage.input == 7
    assert result.usage.cache_read == 3
    assert result.usage.output == 4
    assert transport.request is not None
    assert transport.request.url == "https://example.invalid/v1/chat/completions"
    assert transport.request.headers["Authorization"] == "Bearer secret"
    body = json.loads(transport.request.body)
    assert body["messages"][:2] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ]
    assert body["tools"][0]["function"]["name"] == "echo"


@pytest.mark.asyncio
async def test_openai_provider_accumulates_fragmented_tool_arguments(model: Model) -> None:
    transport = FakeTransport(
        [
            data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "function": {"name": "echo", "arguments": '{"value":"o'},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
            data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": 'ne"}'}}]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ),
            "data: [DONE]",
        ]
    )
    stream = OpenAICompatibleProvider(transport=transport).stream(
        model, Context(), StreamOptions(max_retries=0)
    )
    events = [event async for event in stream]
    result = await stream.result()

    assert [event.type for event in events] == [
        "start",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_delta",
        "toolcall_end",
        "done",
    ]
    assert result.stop_reason == "toolUse"
    assert result.content == [ToolCall("call-1", "echo", {"value": "one"})]
    deltas = [event for event in events if isinstance(event, ToolCallDeltaEvent)]
    first_call = deltas[0].partial.content[0]
    assert isinstance(first_call, ToolCall)
    assert first_call.arguments == {"value": "o"}


@pytest.mark.asyncio
async def test_openai_provider_never_throws_network_or_cancellation_errors(model: Model) -> None:
    transport = FakeTransport([data({"error": {"message": "rate limited"}})])
    stream = OpenAICompatibleProvider(transport=transport).stream(
        model, Context(), StreamOptions(max_retries=0)
    )
    events = [event async for event in stream]
    assert isinstance(events[-1], ErrorEvent)
    assert (await stream.result()).error_message == "rate limited"

    token = CancellationToken()
    token.cancel("cancelled by test")
    aborted = OpenAICompatibleProvider(transport=FakeTransport([])).stream(
        model,
        Context(),
        StreamOptions(signal=token),
    )
    aborted_events = [event async for event in aborted]
    assert isinstance(aborted_events[-1], ErrorEvent)
    assert aborted_events[-1].reason == "aborted"


@pytest.mark.asyncio
async def test_openai_provider_retries_retryable_pre_stream_failure(model: Model) -> None:
    failure = ProviderRequestError(
        ProviderErrorDetails(
            message="rate limited",
            status_code=429,
            retryable=True,
            retry_after=0.0,
            raw={"error": {"message": "rate limited"}},
        )
    )
    transport = ScriptedTransport(
        [
            failure,
            [
                data({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
                "data: [DONE]",
            ],
        ]
    )
    stream = OpenAICompatibleProvider(transport=transport).stream(
        model,
        Context(),
        StreamOptions(max_retries=1, retry_base_delay=0),
    )

    events = [event async for event in stream]
    result = await stream.result()

    assert len(transport.requests) == 2
    assert events[-1].type == "done"
    assert result.content == [TextContent("ok")]


@pytest.mark.asyncio
async def test_openai_provider_rejects_excessive_server_retry_delay(model: Model) -> None:
    failure = ProviderRequestError(
        ProviderErrorDetails(
            message="rate limited",
            status_code=429,
            retryable=True,
            retry_after=10.0,
        )
    )
    transport = ScriptedTransport([failure])
    stream = OpenAICompatibleProvider(transport=transport).stream(
        model,
        Context(),
        StreamOptions(max_retries=2, max_retry_delay=1.0),
    )

    events = [event async for event in stream]
    result = await stream.result()

    assert len(transport.requests) == 1
    assert isinstance(events[-1], ErrorEvent)
    assert "exceeds the configured maximum" in (result.error_message or "")
    assert result.diagnostics[0]["retryAfterSeconds"] == 10.0
    assert result.diagnostics[0]["retryable"] is False


@pytest.mark.asyncio
async def test_openai_provider_stream_idle_timeout_is_terminal_error(model: Model) -> None:
    stream = OpenAICompatibleProvider(transport=IdleTransport()).stream(
        model,
        Context(),
        StreamOptions(max_retries=0, stream_idle_timeout=0.01),
    )

    events = [event async for event in stream]
    result = await stream.result()

    assert isinstance(events[-1], ErrorEvent)
    assert result.error_message == "Provider stream was idle for 0.01s"
    assert result.diagnostics[0]["retryable"] is True


@pytest.mark.asyncio
async def test_openai_provider_hooks_headers_cache_and_sampling_overrides(model: Model) -> None:
    transport = FakeTransport(
        [
            data({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
            "data: [DONE]",
        ]
    )
    hooked_model = replace(
        model,
        compat={"supportsPromptCacheKey": True},
        headers={"X-Model": "model", "X-Remove": "remove"},
        sampling_params={"top_p": 0.9, "seed": 1},
    )
    responses: list[tuple[int, str]] = []

    async def on_payload(payload: dict[str, object], seen_model: Model) -> dict[str, object]:
        assert seen_model is hooked_model
        return {**payload, "custom": "payload"}

    async def on_response(response: ProviderResponse, seen_model: Model) -> None:
        assert seen_model is hooked_model
        responses.append((response.status, response.headers["x-request-id"]))

    stream = OpenAICompatibleProvider(api_key="secret", transport=transport).stream(
        hooked_model,
        Context(messages=[UserMessage("hello")]),
        StreamOptions(
            session_id="session-1",
            cache_retention="long",
            metadata={"user_id": "user-1"},
            sampling_params={"top_p": 0.8},
            headers={"Authorization": None, "X-Remove": None, "X-Option": "option"},
            on_payload=on_payload,
            on_response=on_response,
        ),
    )
    await stream.result()

    assert transport.request is not None
    assert transport.request.headers == {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "X-Model": "model",
        "X-Option": "option",
    }
    body = json.loads(transport.request.body)
    assert body["user"] == "user-1"
    assert body["prompt_cache_key"] == "session-1"
    assert body["prompt_cache_retention"] == "24h"
    assert body["top_p"] == 0.8
    assert body["seed"] == 1
    assert body["custom"] == "payload"
    assert responses == [(200, "fixture")]


@pytest.mark.asyncio
async def test_openai_provider_does_not_retry_after_stream_has_started(model: Model) -> None:
    transport = FailsAfterFirstLineTransport()
    stream = OpenAICompatibleProvider(transport=transport).stream(
        model,
        Context(),
        StreamOptions(max_retries=3, retry_base_delay=0),
    )

    events = [event async for event in stream]
    result = await stream.result()

    assert len(transport.requests) == 1
    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "error",
    ]
    assert result.stop_reason == "error"
    assert result.error_message == "connection lost"
    assert result.content == [TextContent("partial")]
    assert result.diagnostics[0]["responseStarted"] is True
