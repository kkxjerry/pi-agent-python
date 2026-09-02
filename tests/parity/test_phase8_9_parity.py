from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pi_agent.agent import (
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
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
    message_text,
)

ROOT = Path(__file__).resolve().parents[2]


def assistant_text(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text)], stop_reason="stop")


def assistant_tools(*calls: ToolCall) -> AssistantMessage:
    return AssistantMessage(content=list(calls), stop_reason="toolUse")


def echo_tool(*, delay_first: bool = False) -> AgentTool:
    async def execute(
        call_id: str,
        arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        if delay_first and call_id == "tool-1":
            import asyncio

            await asyncio.sleep(0.03)
        return AgentToolResult(content=[TextContent(f"echoed:{arguments['value']}")])

    return AgentTool(
        name="echo",
        label="Echo",
        description="Echo a value",
        parameters={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        },
        execute=execute,
    )


def load_expected(name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = [
        json.loads(line)
        for line in (ROOT / "fixtures" / "upstream" / f"{name}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    events = [record["event"] for record in records if record.get("record") == "event"]
    result = next(record for record in records if record.get("record") == "result")
    return events, result


def project_event(event: Any) -> dict[str, Any]:
    if isinstance(event, (MessageStartEvent, MessageEndEvent)):
        message = event.message
        projected: dict[str, Any] = {
            "type": event.type,
            "role": message["role"] if isinstance(message, dict) else message.role,
        }
        if not isinstance(message, dict):
            text = message_text(message)
            if not (
                isinstance(event, MessageStartEvent)
                and isinstance(message, AssistantMessage)
                and not message.content
            ):
                projected["text"] = text
            if isinstance(message, AssistantMessage):
                projected["stopReason"] = message.stop_reason
                projected["toolCallIds"] = [
                    item.id for item in message.content if isinstance(item, ToolCall)
                ]
            elif isinstance(message, ToolResultMessage):
                projected["toolCallId"] = message.tool_call_id
                projected["toolName"] = message.tool_name
                projected["isError"] = message.is_error
        return projected
    if isinstance(event, MessageUpdateEvent):
        return {
            "type": event.type,
            "assistantEventType": event.assistant_message_event.type,
        }
    if isinstance(event, ToolExecutionStartEvent):
        return {
            "type": event.type,
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
        }
    if isinstance(event, ToolExecutionUpdateEvent):
        return {
            "type": event.type,
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
        }
    if isinstance(event, ToolExecutionEndEvent):
        return {
            "type": event.type,
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
            "isError": event.is_error,
        }
    if isinstance(event, TurnEndEvent):
        return {
            "type": event.type,
            "assistantStopReason": event.message.stop_reason,
            "toolResultIds": [result.tool_call_id for result in event.tool_results],
        }
    if event.type == "agent_end":
        return {
            "type": event.type,
            "messageRoles": [
                message["role"] if isinstance(message, dict) else message.role
                for message in event.messages
            ],
            "toolResultIds": [
                message.tool_call_id
                for message in event.messages
                if isinstance(message, ToolResultMessage)
            ],
        }
    return {"type": event.type}


def project_result(messages: list[Any]) -> dict[str, Any]:
    return {
        "record": "result",
        "messageRoles": [
            message["role"] if isinstance(message, dict) else message.role for message in messages
        ],
        "toolResultIds": [
            message.tool_call_id for message in messages if isinstance(message, ToolResultMessage)
        ],
    }


@pytest.mark.asyncio
async def test_parallel_out_of_order_matches_executed_upstream_fixture(model: Model) -> None:
    provider = FauxProvider(
        [
            assistant_tools(
                ToolCall("tool-1", "echo", {"value": "first"}),
                ToolCall("tool-2", "echo", {"value": "second"}),
            ),
            assistant_text("done"),
        ]
    )
    stream = agent_loop(
        [UserMessage("echo both")],
        AgentContext(system_prompt="fixture", tools=[echo_tool(delay_first=True)]),
        AgentLoopConfig(
            model=model,
            convert_to_llm=default_convert_to_llm,
            tool_execution="parallel",
        ),
        stream_fn=provider.stream,
    )
    events = [event async for event in stream]
    messages = await stream.result()
    expected_events, expected_result = load_expected("parallel_out_of_order")

    assert [project_event(event) for event in events] == expected_events
    assert project_result(messages) == {
        key: value for key, value in expected_result.items() if key != "observations"
    }


@pytest.mark.asyncio
async def test_steering_matches_executed_upstream_fixture(model: Model) -> None:
    delivered = False
    executed = False

    async def steering() -> list[Any]:
        nonlocal delivered
        if delivered or not executed:
            return []
        delivered = True
        return [UserMessage("steer-now")]

    async def after_tool(_context: Any, _signal: Any) -> None:
        nonlocal executed
        executed = True

    provider = FauxProvider(
        [
            assistant_tools(ToolCall("tool-1", "echo", {"value": "one"})),
            assistant_text("steered"),
        ]
    )
    stream = agent_loop(
        [UserMessage("start")],
        AgentContext(system_prompt="fixture", tools=[echo_tool()]),
        AgentLoopConfig(
            model=model,
            convert_to_llm=default_convert_to_llm,
            get_steering_messages=steering,
            after_tool_call=after_tool,
        ),
        stream_fn=provider.stream,
    )
    events = [event async for event in stream]
    messages = await stream.result()
    expected_events, expected_result = load_expected("steering_one")

    assert [project_event(event) for event in events] == expected_events
    assert project_result(messages) == expected_result


@pytest.mark.asyncio
async def test_follow_up_matches_executed_upstream_fixture(model: Model) -> None:
    delivered = False

    async def follow_up() -> list[Any]:
        nonlocal delivered
        if delivered:
            return []
        delivered = True
        return [UserMessage("follow-up")]

    provider = FauxProvider([assistant_text("initial"), assistant_text("followed")])
    stream = agent_loop(
        [UserMessage("start")],
        AgentContext(system_prompt="fixture"),
        AgentLoopConfig(
            model=model,
            convert_to_llm=default_convert_to_llm,
            get_follow_up_messages=follow_up,
        ),
        stream_fn=provider.stream,
    )
    events = [event async for event in stream]
    messages = await stream.result()
    expected_events, expected_result = load_expected("follow_up_one")

    assert [project_event(event) for event in events] == expected_events
    assert project_result(messages) == expected_result
