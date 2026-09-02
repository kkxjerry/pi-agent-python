from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from pi_agent.agent import (
    Agent,
    AgentContext,
    AgentLoopConfig,
    AgentOptions,
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


def assistant_text(value: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(value)], stop_reason="stop")


def assistant_tools(*calls: ToolCall) -> AssistantMessage:
    return AssistantMessage(content=list(calls), stop_reason="toolUse")


def read_golden(name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = [
        json.loads(line)
        for line in (ROOT / "fixtures" / "upstream" / f"{name}.jsonl").read_text().splitlines()
        if line.strip()
    ]
    events = [record["event"] for record in records if record["record"] == "event"]
    result = next(record for record in records if record["record"] == "result")
    return events, {
        "messageRoles": result["messageRoles"],
        "toolResultIds": result["toolResultIds"],
    }


def project_event(event: Any) -> dict[str, Any]:
    if isinstance(event, (MessageStartEvent, MessageEndEvent)):
        projected: dict[str, Any] = {"type": event.type, "role": event.message.role}
        text = message_text(event.message)
        if text is not None and (
            text
            or not isinstance(event.message, AssistantMessage)
            or event.message.stop_reason != "pending"
        ):
            projected["text"] = text
        if isinstance(event.message, AssistantMessage):
            projected["stopReason"] = event.message.stop_reason
            projected["toolCallIds"] = [
                block.id for block in event.message.content if isinstance(block, ToolCall)
            ]
        if isinstance(event.message, ToolResultMessage):
            projected.update(
                {
                    "toolCallId": event.message.tool_call_id,
                    "toolName": event.message.tool_name,
                    "isError": event.message.is_error,
                }
            )
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
            "toolResultIds": [item.tool_call_id for item in event.tool_results],
        }
    if event.type == "agent_end":
        return {
            "type": event.type,
            "messageRoles": [message.role for message in event.messages],
            "toolResultIds": [
                message.tool_call_id
                for message in event.messages
                if isinstance(message, ToolResultMessage)
            ],
        }
    return {"type": event.type}


def project_result(messages: list[Any]) -> dict[str, Any]:
    return {
        "messageRoles": [message.role for message in messages],
        "toolResultIds": [
            message.tool_call_id for message in messages if isinstance(message, ToolResultMessage)
        ],
    }


def echo_tool(*, delayed_first: bool = False) -> AgentTool:
    async def execute(
        call_id: str,
        arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        if delayed_first and call_id == "tool-1":
            await asyncio.sleep(0.03)
        return AgentToolResult(content=[TextContent(f"echoed:{arguments['value']}")])

    return AgentTool(
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


@pytest.mark.asyncio
async def test_parallel_out_of_order_matches_executed_upstream(model: Model) -> None:
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
        AgentContext(system_prompt="fixture", tools=[echo_tool(delayed_first=True)]),
        AgentLoopConfig(
            model=model,
            convert_to_llm=default_convert_to_llm,
            tool_execution="parallel",
        ),
        stream_fn=provider.stream,
    )
    events = [event async for event in stream]
    result = await stream.result()
    expected_events, expected_result = read_golden("parallel_out_of_order")

    assert [project_event(event) for event in events] == expected_events
    assert project_result(result) == expected_result


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["steering_one", "follow_up_one"])
async def test_stateful_queued_messages_match_executed_upstream(
    model: Model,
    scenario: str,
) -> None:
    if scenario == "steering_one":
        provider = FauxProvider(
            [
                assistant_tools(ToolCall("tool-1", "echo", {"value": "one"})),
                assistant_text("steered"),
            ]
        )
        agent = Agent(
            AgentOptions(
                model=model,
                stream_fn=provider.stream,
                system_prompt="fixture",
                tools=[echo_tool()],
            )
        )
        steering_injected = False

        def inject_steering(event: Any) -> None:
            nonlocal steering_injected
            if isinstance(event, ToolExecutionEndEvent) and not steering_injected:
                steering_injected = True
                agent.steer("steer-now")

        agent.subscribe(inject_steering)
    else:
        provider = FauxProvider([assistant_text("initial"), assistant_text("followed")])
        agent = Agent(AgentOptions(model=model, stream_fn=provider.stream, system_prompt="fixture"))
        agent.follow_up("follow-up")

    events: list[Any] = []
    agent.subscribe(events.append)
    result = await agent.prompt("start")
    expected_events, expected_result = read_golden(scenario)

    assert [project_event(event) for event in events] == expected_events
    assert project_result(result) == expected_result
