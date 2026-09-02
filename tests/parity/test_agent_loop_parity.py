from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from pi_agent.agent import (
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    agent_loop,
    agent_loop_continue,
    default_convert_to_llm,
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
from pi_agent.parity import load_jsonl, project_event, project_messages

Scenario = Callable[[Model], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]]


def assistant_text(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text)], stop_reason="stop")


def assistant_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    stop_reason: str = "toolUse",
) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall("tool-1", name, arguments)],
        stop_reason=stop_reason,  # type: ignore[arg-type]
    )


def config(model: Model, **kwargs: Any) -> AgentLoopConfig:
    return AgentLoopConfig(model=model, convert_to_llm=default_convert_to_llm, **kwargs)


async def collect(stream: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events = [project_event(event) async for event in stream]
    return events, project_messages(await stream.result())


def echo_tool(
    *,
    on_execute: Callable[[str], None] | None = None,
    fail: bool = False,
) -> AgentTool:
    async def execute(
        _call_id: str,
        arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        value = str(arguments["value"])
        if on_execute is not None:
            on_execute(value)
        if fail:
            raise RuntimeError("fixture failure")
        return AgentToolResult(content=[TextContent(f"echoed:{value}")])

    return AgentTool(
        name="echo",
        label="Echo",
        description="Echo a value",
        parameters={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
        execute=execute,
    )


async def text_only(model: Model) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider = FauxProvider([assistant_text("hello back")])
    return await collect(
        agent_loop(
            [UserMessage("hello")],
            AgentContext(system_prompt="fixture"),
            config(model),
            stream_fn=provider.stream,
        )
    )


async def single_tool(model: Model) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider = FauxProvider([assistant_tool("echo", {"value": "one"}), assistant_text("done")])
    return await collect(
        agent_loop(
            [UserMessage("echo one")],
            AgentContext(system_prompt="fixture", tools=[echo_tool()]),
            config(model),
            stream_fn=provider.stream,
        )
    )


async def parallel_out_of_order(
    model: Model,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    completion_order: list[str] = []

    async def execute(
        call_id: str,
        arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        if arguments["value"] == "first":
            await asyncio.sleep(0.02)
        completion_order.append(call_id)
        return AgentToolResult(content=[TextContent(f"echoed:{arguments['value']}")])

    tool = AgentTool(
        name="echo",
        label="Echo",
        description="Echo a value",
        parameters={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
        execute=execute,
    )
    provider = FauxProvider(
        [
            AssistantMessage(
                content=[
                    ToolCall("tool-1", "echo", {"value": "first"}),
                    ToolCall("tool-2", "echo", {"value": "second"}),
                ],
                stop_reason="toolUse",
            ),
            assistant_text("done"),
        ]
    )
    events, result = await collect(
        agent_loop(
            [UserMessage("echo both")],
            AgentContext(system_prompt="fixture", tools=[tool]),
            config(model, tool_execution="parallel"),
            stream_fn=provider.stream,
        )
    )
    result["observations"] = {
        "completionOrder": completion_order,
        "transcriptOrder": result["toolResultIds"],
    }
    return events, result


async def invalid_tool_name(model: Model) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider = FauxProvider([assistant_tool("missing", {}), assistant_text("recovered")])
    return await collect(
        agent_loop(
            [UserMessage("call missing")],
            AgentContext(system_prompt="fixture"),
            config(model),
            stream_fn=provider.stream,
        )
    )


async def tool_throws_exception(model: Model) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider = FauxProvider([assistant_tool("echo", {"value": "x"}), assistant_text("recovered")])
    return await collect(
        agent_loop(
            [UserMessage("fail")],
            AgentContext(system_prompt="fixture", tools=[echo_tool(fail=True)]),
            config(model),
            stream_fn=provider.stream,
        )
    )


async def truncated_tool_call(model: Model) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    executed: list[str] = []
    provider = FauxProvider(
        [
            assistant_tool("echo", {"value": "hel"}, stop_reason="length"),
            assistant_text("reissued later"),
        ]
    )
    events, result = await collect(
        agent_loop(
            [UserMessage("truncated")],
            AgentContext(
                system_prompt="fixture",
                tools=[echo_tool(on_execute=executed.append)],
            ),
            config(model),
            stream_fn=provider.stream,
        )
    )
    result["observations"] = {"executedValues": executed}
    return events, result


async def transform_context(model: Model) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider = FauxProvider([assistant_text("done")])

    async def transform(messages: list[Any], _signal: Any) -> list[Any]:
        return messages[-1:]

    events, result = await collect(
        agent_loop(
            [UserMessage("new")],
            AgentContext(
                system_prompt="fixture",
                messages=[UserMessage("old-1"), assistant_text("old-2")],
            ),
            config(model, transform_context=transform),
            stream_fn=provider.stream,
        )
    )
    result["observations"] = {
        "providerMessageRoles": [
            [message.role for message in request.context.messages] for request in provider.requests
        ]
    }
    return events, result


async def steering_one(model: Model) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    executed = False
    delivered = False

    def on_execute(_value: str) -> None:
        nonlocal executed
        executed = True

    async def get_steering() -> list[UserMessage]:
        nonlocal delivered
        if executed and not delivered:
            delivered = True
            return [UserMessage("steer-now")]
        return []

    provider = FauxProvider([assistant_tool("echo", {"value": "one"}), assistant_text("steered")])
    return await collect(
        agent_loop(
            [UserMessage("start")],
            AgentContext(
                system_prompt="fixture",
                tools=[echo_tool(on_execute=on_execute)],
            ),
            config(model, get_steering_messages=get_steering),
            stream_fn=provider.stream,
        )
    )


async def follow_up_one(model: Model) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    delivered = False

    async def get_follow_up() -> list[UserMessage]:
        nonlocal delivered
        if delivered:
            return []
        delivered = True
        return [UserMessage("follow-up")]

    provider = FauxProvider([assistant_text("initial"), assistant_text("followed")])
    return await collect(
        agent_loop(
            [UserMessage("start")],
            AgentContext(system_prompt="fixture"),
            config(model, get_follow_up_messages=get_follow_up),
            stream_fn=provider.stream,
        )
    )


async def continue_existing_context(
    model: Model,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    provider = FauxProvider([assistant_text("continued")])
    return await collect(
        agent_loop_continue(
            AgentContext(
                system_prompt="fixture",
                messages=[UserMessage("existing")],
            ),
            config(model),
            stream_fn=provider.stream,
        )
    )


SCENARIOS: dict[str, Scenario] = {
    "continue_existing_context": continue_existing_context,
    "follow_up_one": follow_up_one,
    "invalid_tool_name": invalid_tool_name,
    "parallel_out_of_order": parallel_out_of_order,
    "single_tool": single_tool,
    "steering_one": steering_one,
    "text_only": text_only,
    "tool_throws_exception": tool_throws_exception,
    "transform_context": transform_context,
    "truncated_tool_call": truncated_tool_call,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_name", sorted(SCENARIOS))
async def test_python_phase_7_8_matches_executed_upstream_fixture(
    model: Model,
    scenario_name: str,
) -> None:
    records = load_jsonl(Path("fixtures/upstream") / f"{scenario_name}.jsonl")
    assert records[0]["provenance"] == "upstream-execution"
    expected_events = [record["event"] for record in records if record.get("record") == "event"]
    expected_result = next(
        {key: value for key, value in record.items() if key != "record"}
        for record in records
        if record.get("record") == "result"
    )

    actual_events, actual_result = await SCENARIOS[scenario_name](model)

    assert actual_events == expected_events
    assert actual_result == expected_result
