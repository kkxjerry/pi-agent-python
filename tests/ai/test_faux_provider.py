from __future__ import annotations

import pytest

from pi_agent.ai import (
    AssistantMessage,
    CancellationToken,
    Context,
    ErrorEvent,
    FauxProvider,
    Model,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    UserMessage,
)


@pytest.mark.asyncio
async def test_faux_provider_streams_all_content_kinds_and_records_request(model: Model) -> None:
    expected = AssistantMessage(
        content=[
            ThinkingContent("plan"),
            TextContent("hello"),
            ToolCall("call-1", "echo", {"value": "one"}),
        ],
        stop_reason="toolUse",
    )
    provider = FauxProvider([expected], chunk_size=2)
    stream = provider.stream(model, Context(messages=[UserMessage("go")]))
    events = [event async for event in stream]
    result = await stream.result()

    assert events[0].type == "start"
    assert events[-1].type == "done"
    assert [event.type for event in events].count("text_delta") == 3
    assert [event.type for event in events].count("thinking_delta") == 2
    assert result.content == expected.content
    assert result.model == model.id
    assert provider.requests[0].context.messages[0].role == "user"


@pytest.mark.asyncio
async def test_faux_provider_encodes_exception_and_abort_as_terminal_events(model: Model) -> None:
    failed = FauxProvider([RuntimeError("boom")]).stream(model, Context())
    failed_events = [event async for event in failed]
    assert isinstance(failed_events[-1], ErrorEvent)
    assert (await failed.result()).error_message == "boom"

    token = CancellationToken()
    token.cancel("stop now")
    aborted = FauxProvider([AssistantMessage(stop_reason="stop")]).stream(
        model,
        Context(),
        StreamOptions(signal=token),
    )
    aborted_events = [event async for event in aborted]
    assert isinstance(aborted_events[-1], ErrorEvent)
    assert aborted_events[-1].reason == "aborted"
