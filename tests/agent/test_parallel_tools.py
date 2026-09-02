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
    MessageEndEvent,
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


def tool_message(*calls: ToolCall) -> AssistantMessage:
    return AssistantMessage(content=list(calls), stop_reason="toolUse")


def final_message(text: str = "done") -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text)], stop_reason="stop")


def loop_config(model: Model, **kwargs: Any) -> AgentLoopConfig:
    return AgentLoopConfig(model=model, convert_to_llm=default_convert_to_llm, **kwargs)


@pytest.mark.asyncio
async def test_parallel_completion_events_and_transcript_have_distinct_orders(model: Model) -> None:
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
            tool_message(
                ToolCall("tool-1", "echo", {"value": "first"}),
                ToolCall("tool-2", "echo", {"value": "second"}),
            ),
            final_message(),
        ]
    )
    stream = agent_loop(
        [UserMessage("run")],
        AgentContext(tools=[tool]),
        loop_config(model, tool_execution="parallel"),
        stream_fn=provider.stream,
    )
    events = [event async for event in stream]
    result = await stream.result()

    end_order = [event.tool_call_id for event in events if isinstance(event, ToolExecutionEndEvent)]
    message_order = [
        event.message.tool_call_id
        for event in events
        if isinstance(event, MessageEndEvent) and isinstance(event.message, ToolResultMessage)
    ]
    transcript_order = [
        message.tool_call_id for message in result if isinstance(message, ToolResultMessage)
    ]
    assert completion_order == ["tool-2", "tool-1"]
    assert end_order == ["tool-2", "tool-1"]
    assert message_order == ["tool-1", "tool-2"]
    assert transcript_order == ["tool-1", "tool-2"]
    assert [
        message.tool_call_id
        for message in provider.requests[1].context.messages
        if isinstance(message, ToolResultMessage)
    ] == ["tool-1", "tool-2"]


@pytest.mark.asyncio
async def test_one_sequential_tool_forces_the_entire_batch_to_run_serially(model: Model) -> None:
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

    serial = AgentTool(
        name="serial",
        label="Serial",
        description="Serial",
        parameters={"type": "object"},
        execute=execute,
        execution_mode="sequential",
    )
    parallel = AgentTool(
        name="parallel",
        label="Parallel",
        description="Parallel",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider(
        [
            tool_message(
                ToolCall("one", "parallel", {}),
                ToolCall("two", "serial", {}),
            ),
            final_message(),
        ]
    )
    await agent_loop(
        [UserMessage("run")],
        AgentContext(tools=[serial, parallel]),
        loop_config(model, tool_execution="parallel"),
        stream_fn=provider.stream,
    ).result()

    assert max_active == 1
    assert order == ["start:one", "end:one", "start:two", "end:two"]


@pytest.mark.asyncio
async def test_before_and_after_tool_hooks_block_and_override(model: Model) -> None:
    executed: list[str] = []

    async def execute(
        call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        executed.append(call_id)
        return AgentToolResult(content=[TextContent("raw")], details={"stage": "raw"})

    tool = AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
    )

    async def before(context: Any, _signal: Any) -> BeforeToolCallResult | None:
        if context.tool_call.id == "blocked":
            return BeforeToolCallResult(block=True, reason="policy blocked")
        return None

    async def after(context: Any, _signal: Any) -> AfterToolCallResult:
        assert context.result.content == [TextContent("raw")]
        return AfterToolCallResult(
            content=[TextContent("overridden")],
            details={"stage": "after"},
            is_error=True,
        )

    provider = FauxProvider(
        [
            tool_message(
                ToolCall("blocked", "work", {}),
                ToolCall("allowed", "work", {}),
            ),
            final_message(),
        ]
    )
    result = await agent_loop(
        [UserMessage("run")],
        AgentContext(tools=[tool]),
        loop_config(model, before_tool_call=before, after_tool_call=after),
        stream_fn=provider.stream,
    ).result()

    assert executed == ["allowed"]
    tool_results = [message for message in result if isinstance(message, ToolResultMessage)]
    assert tool_results[0].content == [TextContent("policy blocked")]
    assert tool_results[0].is_error is True
    assert tool_results[1].content == [TextContent("overridden")]
    assert tool_results[1].details == {"stage": "after"}
    assert tool_results[1].is_error is True


@pytest.mark.asyncio
async def test_should_stop_after_turn_finishes_current_tool_batch(model: Model) -> None:
    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        return AgentToolResult(content=[TextContent("done")])

    tool = AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider([tool_message(ToolCall("one", "work", {}))])
    observed: list[int] = []

    async def stop(context: Any) -> bool:
        observed.append(len(context.tool_results))
        return True

    result = await agent_loop(
        [UserMessage("run")],
        AgentContext(tools=[tool]),
        loop_config(model, should_stop_after_turn=stop),
        stream_fn=provider.stream,
    ).result()

    assert observed == [1]
    assert len(provider.requests) == 1
    assert any(isinstance(message, ToolResultMessage) for message in result)
