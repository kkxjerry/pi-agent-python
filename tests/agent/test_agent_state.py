from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pi_agent.agent import (
    Agent,
    AgentOptions,
    AgentTool,
    AgentToolResult,
    MessageEndEvent,
    ToolExecutionStartEvent,
)
from pi_agent.ai import (
    AssistantMessage,
    CancellationToken,
    FauxProvider,
    Model,
    TextContent,
    ToolCall,
    UserMessage,
)


def text(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text)], stop_reason="stop")


def tool_call(call_id: str = "tool-1") -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(call_id, "work", {})],
        stop_reason="toolUse",
    )


@pytest.mark.asyncio
async def test_state_is_reduced_before_listeners_and_agent_end_is_a_barrier(model: Model) -> None:
    provider = FauxProvider([text("done")])
    agent = Agent(AgentOptions(model=model, stream_fn=provider.stream))
    release = asyncio.Event()
    observations: list[tuple[str, bool, str | None]] = []

    async def listener(event: Any) -> None:
        streaming = agent.streaming_message
        observations.append(
            (
                event.type,
                agent.is_streaming,
                streaming.role
                if streaming is not None and not isinstance(streaming, dict)
                else None,
            )
        )
        if event.type == "agent_end":
            await release.wait()

    agent.subscribe(listener)
    task = asyncio.create_task(agent.prompt("hello"))
    while not observations or observations[-1][0] != "agent_end":
        await asyncio.sleep(0)
    assert agent.is_streaming is True
    release.set()
    result = await task

    assert agent.is_streaming is False
    assert [message.role for message in result] == ["user", "assistant"]
    assert [message.role for message in agent.messages] == ["user", "assistant"]
    assert any(kind == "message_update" and role == "assistant" for kind, _, role in observations)
    assert all(streaming for _, streaming, _ in observations)


@pytest.mark.asyncio
async def test_steering_one_at_a_time_and_all_modes(model: Model) -> None:
    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        return AgentToolResult(content=[TextContent("worked")])

    tool = AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider([tool_call(), text("done")])
    agent = Agent(
        AgentOptions(
            model=model,
            stream_fn=provider.stream,
            tools=[tool],
            steering_mode="one-at-a-time",
        )
    )
    agent.steer("first steer")
    agent.steer("second steer")
    await agent.prompt("start")

    assert [message.content for message in agent.messages if isinstance(message, UserMessage)] == [
        "start",
        "first steer",
        "second steer",
    ]
    assert [
        message.content
        for message in provider.requests[0].context.messages
        if isinstance(message, UserMessage)
    ] == ["start", "first steer"]
    assert [
        message.content
        for message in provider.requests[1].context.messages
        if isinstance(message, UserMessage)
    ] == ["start", "first steer", "second steer"]

    all_provider = FauxProvider([tool_call(), text("done")])
    all_agent = Agent(
        AgentOptions(
            model=model,
            stream_fn=all_provider.stream,
            tools=[tool],
            steering_mode="all",
        )
    )
    all_agent.steer("one")
    all_agent.steer("two")
    await all_agent.prompt("start")
    assert [
        message.content
        for message in all_provider.requests[1].context.messages
        if isinstance(message, UserMessage)
    ] == ["start", "one", "two"]


@pytest.mark.asyncio
async def test_follow_up_waits_until_normal_stop(model: Model) -> None:
    provider = FauxProvider([text("initial"), text("followed")])
    agent = Agent(AgentOptions(model=model, stream_fn=provider.stream))
    agent.follow_up("follow-up")
    result = await agent.prompt("start")

    assert [message.role for message in result] == ["user", "assistant", "user", "assistant"]
    assert [
        message.content
        for message in provider.requests[1].context.messages
        if isinstance(message, UserMessage)
    ] == ["start", "follow-up"]


@pytest.mark.asyncio
async def test_pending_tool_state_and_listener_failures_do_not_break_run(model: Model) -> None:
    observed_pending: list[frozenset[str]] = []

    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        await asyncio.sleep(0)
        return AgentToolResult(content=[TextContent("done")])

    tool = AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider([tool_call(), text("done")])
    agent = Agent(AgentOptions(model=model, stream_fn=provider.stream, tools=[tool]))

    def listener(event: Any) -> None:
        if isinstance(event, ToolExecutionStartEvent):
            observed_pending.append(agent.pending_tool_calls)
        if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
            raise RuntimeError("listener failure")

    agent.subscribe(listener)
    await agent.prompt("run")

    assert observed_pending == [frozenset({"tool-1"})]
    assert agent.pending_tool_calls == frozenset()
    assert len(agent.listener_errors) == 2
    assert agent.error_message is None


@pytest.mark.asyncio
async def test_agent_rejects_overlapping_runs_and_can_be_reused(model: Model) -> None:
    gate = asyncio.Event()

    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        await gate.wait()
        return AgentToolResult(content=[TextContent("done")])

    tool = AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider([tool_call(), text("first done"), text("second done")])
    agent = Agent(AgentOptions(model=model, stream_fn=provider.stream, tools=[tool]))
    first = asyncio.create_task(agent.prompt("first"))
    while not agent.pending_tool_calls:
        await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="already streaming"):
        await agent.prompt("overlap")
    gate.set()
    await first
    second = await agent.prompt("second")
    assert isinstance(second[-1], AssistantMessage)
    assert second[-1].content == [TextContent("second done")]
