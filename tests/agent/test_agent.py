from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pi_agent.agent import (
    Agent,
    AgentOptions,
    AgentState,
    AgentTool,
    AgentToolResult,
    MessageEndEvent,
    PendingMessageQueue,
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


def assistant_text(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text)], stop_reason="stop")


def assistant_tool(name: str, arguments: dict[str, Any]) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall("tool-1", name, arguments)],
        stop_reason="toolUse",
    )


def make_agent(model: Model, provider: FauxProvider, **kwargs: Any) -> Agent:
    return Agent(
        AgentOptions(
            stream_fn=provider.stream,
            initial_state=AgentState(model=model),
            **kwargs,
        )
    )


@pytest.mark.asyncio
async def test_state_is_reduced_before_ordered_listeners_and_idle_waits_for_agent_end(
    model: Model,
) -> None:
    provider = FauxProvider([assistant_text("done")])
    agent = make_agent(model, provider)
    entered_agent_end = asyncio.Event()
    release_agent_end = asyncio.Event()
    listener_order: list[str] = []

    async def first_listener(event: Any, _signal: CancellationToken) -> None:
        listener_order.append(f"first:{event.type}")
        if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
            assert agent.state.messages[-1] is event.message
        if event.type == "agent_end":
            entered_agent_end.set()
            await release_agent_end.wait()

    async def second_listener(event: Any, _signal: CancellationToken) -> None:
        listener_order.append(f"second:{event.type}")

    agent.subscribe(first_listener)
    agent.subscribe(second_listener)

    prompt_task = asyncio.create_task(agent.prompt("hello"))
    await entered_agent_end.wait()
    idle_task = asyncio.create_task(agent.wait_for_idle())
    await asyncio.sleep(0)

    assert agent.state.is_streaming is True
    assert prompt_task.done() is False
    assert idle_task.done() is False
    assert listener_order[-1] == "first:agent_end"

    release_agent_end.set()
    await prompt_task
    await idle_task

    assert agent.state.is_streaming is False
    assert listener_order[-2:] == ["first:agent_end", "second:agent_end"]
    assert [message.role for message in agent.state.messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_steering_is_injected_after_current_tool_turn(model: Model) -> None:
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    async def execute(
        _call_id: str,
        arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        tool_started.set()
        await release_tool.wait()
        return AgentToolResult(content=[TextContent(f"echoed:{arguments['value']}")])

    tool = AgentTool(
        name="echo",
        label="Echo",
        description="Echo",
        parameters={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        },
        execute=execute,
    )
    provider = FauxProvider([assistant_tool("echo", {"value": "one"}), assistant_text("steered")])
    agent = make_agent(model, provider)
    agent.state.tools = [tool]

    task = asyncio.create_task(agent.prompt("start"))
    await tool_started.wait()
    agent.steer(UserMessage("steer-now"))
    release_tool.set()
    await task

    assert [message.role for message in provider.requests[1].context.messages] == [
        "user",
        "assistant",
        "toolResult",
        "user",
    ]
    assert isinstance(provider.requests[1].context.messages[-1], UserMessage)
    assert provider.requests[1].context.messages[-1].content == "steer-now"
    assert [message.role for message in agent.state.messages] == [
        "user",
        "assistant",
        "toolResult",
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_follow_up_waits_until_agent_would_otherwise_stop(model: Model) -> None:
    provider = FauxProvider([assistant_text("initial"), assistant_text("followed")])
    agent = make_agent(model, provider)
    agent.follow_up(UserMessage("follow-up"))

    await agent.prompt("start")

    assert len(provider.requests) == 2
    assert [message.role for message in provider.requests[1].context.messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert isinstance(provider.requests[1].context.messages[-1], UserMessage)
    assert provider.requests[1].context.messages[-1].content == "follow-up"


@pytest.mark.asyncio
async def test_abort_clears_runtime_state_and_agent_can_be_reused(model: Model) -> None:
    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        assert signal is not None
        signal.raise_if_cancelled()
        return AgentToolResult(content=[TextContent("unexpected")])

    tool = AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
        execution_mode="sequential",
    )
    provider = FauxProvider([assistant_tool("work", {}), assistant_text("reused")])
    agent = make_agent(model, provider, tool_execution="sequential")
    agent.state.tools = [tool]

    async def abort_on_start(event: Any, _signal: CancellationToken) -> None:
        if isinstance(event, ToolExecutionStartEvent):
            assert event.tool_call_id in agent.state.pending_tool_calls
            agent.abort("cancelled by test")

    agent.subscribe(abort_on_start)
    await agent.prompt("first")

    assert agent.state.is_streaming is False
    assert not agent.state.pending_tool_calls
    assert agent.state.error_message == "cancelled by test"

    agent.state.tools = []
    await agent.prompt("second")

    assert agent.state.is_streaming is False
    assert isinstance(agent.state.messages[-1], AssistantMessage)
    assert agent.state.messages[-1].content == [TextContent("reused")]


@pytest.mark.asyncio
async def test_continue_uses_existing_non_assistant_tail(model: Model) -> None:
    provider = FauxProvider([assistant_text("continued")])
    state = AgentState(model=model, messages=[UserMessage("existing")])
    agent = Agent(AgentOptions(stream_fn=provider.stream, initial_state=state))

    await agent.continue_run()

    assert [message.role for message in agent.state.messages] == ["user", "assistant"]
    assert [message.role for message in provider.requests[0].context.messages] == ["user"]


@pytest.mark.asyncio
async def test_continue_from_assistant_drains_queued_message(model: Model) -> None:
    provider = FauxProvider([assistant_text("continued from queue")])
    state = AgentState(model=model, messages=[assistant_text("existing")])
    agent = Agent(AgentOptions(stream_fn=provider.stream, initial_state=state))
    agent.steer(UserMessage("queued steering"))

    await agent.continue_run()

    assert [message.role for message in provider.requests[0].context.messages] == [
        "assistant",
        "user",
    ]
    assert agent.has_queued_messages() is False


def test_state_array_assignment_and_pending_queues_are_defensive() -> None:
    state = AgentState()
    source_messages = [UserMessage("one")]
    state.messages = source_messages
    source_messages.append(UserMessage("two"))
    assert len(state.messages) == 1

    one = PendingMessageQueue("one-at-a-time")
    one.enqueue(UserMessage("a"))
    one.enqueue(UserMessage("b"))
    assert [message.content for message in one.drain() if isinstance(message, UserMessage)] == ["a"]
    assert len(one) == 1

    all_messages = PendingMessageQueue("all")
    all_messages.enqueue(UserMessage("a"))
    all_messages.enqueue(UserMessage("b"))
    assert [
        message.content for message in all_messages.drain() if isinstance(message, UserMessage)
    ] == ["a", "b"]
    assert len(all_messages) == 0
