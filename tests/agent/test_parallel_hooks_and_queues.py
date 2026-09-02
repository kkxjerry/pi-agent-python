from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pi_agent.agent import (
    AfterToolCallResult,
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    BeforeToolCallResult,
    MessageStartEvent,
    ToolExecutionEndEvent,
    agent_loop,
    default_convert_to_llm,
)
from pi_agent.ai import (
    AssistantMessage,
    CancellationToken,
    FauxProvider,
    Model,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def assistant_text(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text)], stop_reason="stop")


def assistant_tools(*calls: ToolCall) -> AssistantMessage:
    return AssistantMessage(content=list(calls), stop_reason="toolUse")


def config(model: Model, **kwargs: Any) -> AgentLoopConfig:
    return AgentLoopConfig(model=model, convert_to_llm=default_convert_to_llm, **kwargs)


async def collect(stream: Any) -> tuple[list[Any], list[Any]]:
    events = [event async for event in stream]
    return events, await stream.result()


@pytest.mark.asyncio
async def test_parallel_completion_events_are_out_of_order_but_results_are_source_order(
    model: Model,
) -> None:
    completion_order: list[str] = []

    async def execute(
        call_id: str,
        arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        if arguments["value"] == "first":
            await asyncio.sleep(0.03)
        completion_order.append(call_id)
        return AgentToolResult(content=[TextContent(str(arguments["value"]))])

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
    provider = FauxProvider(
        [
            assistant_tools(
                ToolCall("tool-1", "echo", {"value": "first"}),
                ToolCall("tool-2", "echo", {"value": "second"}),
            ),
            assistant_text("done"),
        ]
    )
    events, messages = await collect(
        agent_loop(
            [UserMessage("go")],
            AgentContext(tools=[tool]),
            config(model, tool_execution="parallel"),
            stream_fn=provider.stream,
        )
    )

    end_order = [event.tool_call_id for event in events if isinstance(event, ToolExecutionEndEvent)]
    result_event_order = [
        event.message.tool_call_id
        for event in events
        if isinstance(event, MessageStartEvent) and isinstance(event.message, ToolResultMessage)
    ]
    transcript_order = [
        message.tool_call_id for message in messages if isinstance(message, ToolResultMessage)
    ]
    assert completion_order == ["tool-2", "tool-1"]
    assert end_order == ["tool-2", "tool-1"]
    assert result_event_order == ["tool-1", "tool-2"]
    assert transcript_order == ["tool-1", "tool-2"]
    assert [
        message.tool_call_id
        for message in provider.requests[1].context.messages
        if isinstance(message, ToolResultMessage)
    ] == ["tool-1", "tool-2"]


@pytest.mark.asyncio
async def test_one_sequential_tool_forces_the_whole_batch_to_run_sequentially(model: Model) -> None:
    active = 0
    max_active = 0
    order: list[str] = []

    async def execute(
        call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        order.append(f"start:{call_id}")
        await asyncio.sleep(0.01)
        order.append(f"end:{call_id}")
        active -= 1
        return AgentToolResult(content=[TextContent(call_id)])

    tool = AgentTool(
        name="serial",
        label="Serial",
        description="Serial",
        parameters={"type": "object"},
        execute=execute,
        execution_mode="sequential",
    )
    provider = FauxProvider(
        [
            assistant_tools(
                ToolCall("tool-1", "serial", {}),
                ToolCall("tool-2", "serial", {}),
            ),
            assistant_text("done"),
        ]
    )
    await agent_loop(
        [UserMessage("go")],
        AgentContext(tools=[tool]),
        config(model, tool_execution="parallel"),
        stream_fn=provider.stream,
    ).result()

    assert max_active == 1
    assert order == ["start:tool-1", "end:tool-1", "start:tool-2", "end:tool-2"]


@pytest.mark.asyncio
async def test_before_and_after_tool_hooks_can_block_and_replace_results(model: Model) -> None:
    executed = False

    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        nonlocal executed
        executed = True
        return AgentToolResult(content=[TextContent("raw")], details={"raw": True})

    tool = AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
    )

    async def block(_context: Any, _signal: Any) -> BeforeToolCallResult:
        return BeforeToolCallResult(block=True, reason="policy denied", terminate=True)

    blocked_provider = FauxProvider([assistant_tools(ToolCall("tool-1", "work", {}))])
    blocked = await agent_loop(
        [UserMessage("go")],
        AgentContext(tools=[tool]),
        config(model, before_tool_call=block),
        stream_fn=blocked_provider.stream,
    ).result()
    blocked_result = next(item for item in blocked if isinstance(item, ToolResultMessage))
    assert executed is False
    assert blocked_result.content == [TextContent("policy denied")]
    assert blocked_result.is_error is True
    assert len(blocked_provider.requests) == 1

    async def override(_context: Any, _signal: Any) -> AfterToolCallResult:
        return AfterToolCallResult.replacing_details(
            {"reviewed": True},
            content=[TextContent("reviewed")],
            is_error=True,
            terminate=True,
        )

    override_provider = FauxProvider([assistant_tools(ToolCall("tool-2", "work", {}))])
    overridden = await agent_loop(
        [UserMessage("go")],
        AgentContext(tools=[tool]),
        config(model, after_tool_call=override),
        stream_fn=override_provider.stream,
    ).result()
    overridden_result = next(item for item in overridden if isinstance(item, ToolResultMessage))
    assert executed is True
    assert overridden_result.content == [TextContent("reviewed")]
    assert overridden_result.details == {"reviewed": True}
    assert overridden_result.is_error is True
    assert len(override_provider.requests) == 1


@pytest.mark.asyncio
async def test_tool_timeout_becomes_error_result_and_the_model_can_recover(model: Model) -> None:
    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        await asyncio.sleep(1)
        return AgentToolResult(content=[TextContent("late")])

    tool = AgentTool(
        name="slow",
        label="Slow",
        description="Slow",
        parameters={"type": "object"},
        execute=execute,
        timeout=0.01,
    )
    provider = FauxProvider(
        [assistant_tools(ToolCall("tool-1", "slow", {})), assistant_text("recovered")]
    )
    messages = await agent_loop(
        [UserMessage("go")],
        AgentContext(tools=[tool]),
        config(model),
        stream_fn=provider.stream,
    ).result()

    result = next(item for item in messages if isinstance(item, ToolResultMessage))
    assert result.is_error is True
    assert "timed out" in result.content[0].text
    assert isinstance(messages[-1], AssistantMessage)
    assert messages[-1].content == [TextContent("recovered")]


@pytest.mark.asyncio
async def test_steering_precedes_follow_up_and_messages_are_announced_on_next_turn(
    model: Model,
) -> None:
    steering_calls = 0
    follow_calls = 0

    async def steering() -> list[Any]:
        nonlocal steering_calls
        steering_calls += 1
        return [UserMessage("steer-now")] if steering_calls == 1 else []

    async def follow_up() -> list[Any]:
        nonlocal follow_calls
        follow_calls += 1
        return [UserMessage("follow-up")] if follow_calls == 1 else []

    provider = FauxProvider([assistant_text("first"), assistant_text("followed")])
    events, messages = await collect(
        agent_loop(
            [UserMessage("start")],
            AgentContext(),
            config(
                model,
                get_steering_messages=steering,
                get_follow_up_messages=follow_up,
            ),
            stream_fn=provider.stream,
        )
    )

    users = [
        event.message.content
        for event in events
        if isinstance(event, MessageStartEvent) and isinstance(event.message, UserMessage)
    ]
    assert users == ["start", "steer-now", "follow-up"]
    assert [message.content for message in messages if isinstance(message, UserMessage)] == [
        "start",
        "steer-now",
        "follow-up",
    ]
    assert len(provider.requests) == 2
    assert [
        message.content
        for message in provider.requests[0].context.messages
        if isinstance(message, UserMessage)
    ] == ["start", "steer-now"]
    assert [
        message.content
        for message in provider.requests[1].context.messages
        if isinstance(message, UserMessage)
    ] == ["start", "steer-now", "follow-up"]
