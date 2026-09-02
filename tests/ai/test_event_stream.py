from __future__ import annotations

import asyncio

import pytest

from pi_agent.ai import AssistantMessage, DoneEvent, EventStream, TextContent
from pi_agent.ai.event_stream import AssistantMessageEventStream


@pytest.mark.asyncio
async def test_terminal_event_is_yielded_and_result_is_separate() -> None:
    stream = AssistantMessageEventStream()
    message = AssistantMessage(content=[TextContent("done")], stop_reason="stop")
    stream.push(DoneEvent("stop", message))
    stream.push(DoneEvent("stop", AssistantMessage(stop_reason="stop")))

    events = [event async for event in stream]
    assert len(events) == 1
    assert await stream.result() is message
    assert stream.done is True


@pytest.mark.asyncio
async def test_event_stream_waiter_receives_events_in_order() -> None:
    stream: EventStream[str, int] = EventStream(lambda item: item == "done", lambda _: 7)

    async def consume() -> list[str]:
        return [item async for item in stream]

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    stream.push("first")
    stream.push("done")
    assert await task == ["first", "done"]
    assert await stream.result() == 7


@pytest.mark.asyncio
async def test_explicit_end_resolves_once_and_wakes_waiters() -> None:
    stream: EventStream[str, int] = EventStream(lambda item: item == "done", lambda _: 7)

    async def consume() -> list[str]:
        return [item async for item in stream]

    waiter = asyncio.create_task(consume())
    await asyncio.sleep(0)
    stream.end(42)
    stream.end(99)
    assert await waiter == []
    assert await stream.result() == 42


@pytest.mark.asyncio
async def test_terminal_push_result_cannot_be_overwritten_by_end() -> None:
    stream: EventStream[str, int] = EventStream(lambda item: item == "done", lambda _: 7)
    stream.push("done")
    stream.end(99)
    assert [item async for item in stream] == ["done"]
    assert await stream.result() == 7


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_starve_the_next_consumer() -> None:
    stream: EventStream[str, int] = EventStream(lambda item: item == "done", lambda _: 7)
    first_iterator = stream.__aiter__()
    first = asyncio.create_task(anext(first_iterator))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second_iterator = stream.__aiter__()
    second = asyncio.create_task(anext(second_iterator))
    await asyncio.sleep(0)
    stream.push("done")

    assert await second == "done"
    assert await stream.result() == 7
