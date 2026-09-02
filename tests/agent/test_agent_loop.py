from __future__ import annotations

from typing import Any

import pytest

from pi_agent.agent import (
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    MessageStartEvent,
    ToolExecutionEndEvent,
    ToolExecutionUpdateEvent,
    agent_loop,
    agent_loop_continue,
    default_convert_to_llm,
)
from pi_agent.ai import (
    AssistantMessage,
    CancellationToken,
    FauxProvider,
    Model,
    StreamOptions,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def assistant_text(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text)], stop_reason="stop")


def assistant_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    call_id: str = "tool-1",
    stop_reason: str = "toolUse",
) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(call_id, name, arguments)],
        stop_reason=stop_reason,  # type: ignore[arg-type]
    )


def config(model: Model, **kwargs: Any) -> AgentLoopConfig:
    return AgentLoopConfig(model=model, convert_to_llm=default_convert_to_llm, **kwargs)


async def collect(stream: Any) -> tuple[list[Any], list[Any]]:
    events = [event async for event in stream]
    return events, await stream.result()


@pytest.mark.asyncio
async def test_text_only_loop_emits_lifecycle_without_mutating_input_context(model: Model) -> None:
    provider = FauxProvider([assistant_text("hello back")])
    context = AgentContext(system_prompt="system")
    events, result = await collect(
        agent_loop(
            [UserMessage("hello")],
            context,
            config(model),
            stream_fn=provider.stream,
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_update",
        "message_update",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert [message.role for message in result] == ["user", "assistant"]
    assert context.messages == []
    assert provider.requests[0].context.system_prompt == "system"


@pytest.mark.asyncio
async def test_tool_result_is_appended_before_the_next_provider_request(model: Model) -> None:
    executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        call_id: str,
        arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        executed.append((call_id, arguments))
        return AgentToolResult(
            content=[TextContent(f"echoed:{arguments['value']}")],
            details={"value": arguments["value"]},
        )

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
    provider = FauxProvider([assistant_tool("echo", {"value": "one"}), assistant_text("done")])
    context = AgentContext(tools=[tool])
    events, result = await collect(
        agent_loop([UserMessage("echo")], context, config(model), stream_fn=provider.stream)
    )

    assert executed == [("tool-1", {"value": "one"})]
    assert [message.role for message in result] == [
        "user",
        "assistant",
        "toolResult",
        "assistant",
    ]
    assert [message.role for message in provider.requests[1].context.messages] == [
        "user",
        "assistant",
        "toolResult",
    ]
    assert [event.type for event in events].count("turn_start") == 2
    tool_result = next(message for message in result if isinstance(message, ToolResultMessage))
    assert tool_result.is_error is False
    assert tool_result.content == [TextContent("echoed:one")]
    assert context.messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "call", "expected"),
    [
        (None, assistant_tool("missing", {}), "Tool missing not found"),
    ],
)
async def test_unknown_tool_becomes_error_result_and_loop_recovers(
    model: Model,
    tool: AgentTool | None,
    call: AssistantMessage,
    expected: str,
) -> None:
    provider = FauxProvider([call, assistant_text("recovered")])
    context = AgentContext(tools=[] if tool is None else [tool])
    events, result = await collect(
        agent_loop([UserMessage("go")], context, config(model), stream_fn=provider.stream)
    )

    tool_result = next(message for message in result if isinstance(message, ToolResultMessage))
    assert tool_result.is_error is True
    assert tool_result.content == [TextContent(expected)]
    end = next(event for event in events if isinstance(event, ToolExecutionEndEvent))
    assert end.is_error is True
    assert isinstance(result[-1], AssistantMessage)
    assert result[-1].content == [TextContent("recovered")]
    assert result[-1].stop_reason == "stop"


@pytest.mark.asyncio
async def test_invalid_arguments_and_tool_exception_are_returned_to_model(model: Model) -> None:
    executed = False

    async def should_not_execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        nonlocal executed
        executed = True
        raise RuntimeError("fixture failure")

    tool = AgentTool(
        name="echo",
        label="Echo",
        description="Echo",
        parameters={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        },
        execute=should_not_execute,
    )
    invalid_provider = FauxProvider(
        [assistant_tool("echo", {}), assistant_text("recovered invalid")]
    )
    invalid_result = await agent_loop(
        [UserMessage("go")],
        AgentContext(tools=[tool]),
        config(model),
        stream_fn=invalid_provider.stream,
    ).result()
    invalid_tool_result = next(
        message for message in invalid_result if isinstance(message, ToolResultMessage)
    )
    assert "missing required property 'value'" in invalid_tool_result.content[0].text
    assert executed is False

    throwing_provider = FauxProvider(
        [assistant_tool("echo", {"value": "x"}), assistant_text("recovered throw")]
    )
    throwing_result = await agent_loop(
        [UserMessage("go")],
        AgentContext(tools=[tool]),
        config(model),
        stream_fn=throwing_provider.stream,
    ).result()
    thrown = next(message for message in throwing_result if isinstance(message, ToolResultMessage))
    assert thrown.content == [TextContent("fixture failure")]
    assert thrown.is_error is True


@pytest.mark.asyncio
async def test_length_truncated_tool_call_is_never_executed(model: Model) -> None:
    executed: list[str] = []

    async def execute(
        _call_id: str,
        arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        executed.append(str(arguments["value"]))
        return AgentToolResult(content=[TextContent("should not happen")])

    tool = AgentTool(
        name="echo",
        label="Echo",
        description="Echo",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider(
        [
            assistant_tool("echo", {"value": "hel"}, stop_reason="length"),
            assistant_text("reissued later"),
        ]
    )
    result = await agent_loop(
        [UserMessage("truncated")],
        AgentContext(tools=[tool]),
        config(model),
        stream_fn=provider.stream,
    ).result()

    assert executed == []
    tool_result = next(message for message in result if isinstance(message, ToolResultMessage))
    assert tool_result.content == [
        TextContent(
            'Tool call "echo" was not executed: the response hit the output token limit, '
            "so its arguments may be truncated. Re-issue the tool call with complete arguments."
        )
    ]


@pytest.mark.asyncio
async def test_continue_emits_no_existing_user_message_event(model: Model) -> None:
    context = AgentContext(messages=[UserMessage("existing")])
    provider = FauxProvider([assistant_text("continued")])
    events, result = await collect(
        agent_loop_continue(context, config(model), stream_fn=provider.stream)
    )

    starts = [event for event in events if isinstance(event, MessageStartEvent)]
    assert all(not isinstance(event.message, UserMessage) for event in starts)
    assert [message.role for message in result] == ["assistant"]
    assert [message.role for message in context.messages] == ["user", "assistant"]

    with pytest.raises(ValueError, match="empty"):
        agent_loop_continue(AgentContext(), config(model), stream_fn=provider.stream)
    with pytest.raises(ValueError, match="assistant"):
        agent_loop_continue(
            AgentContext(messages=[assistant_text("last")]),
            config(model),
            stream_fn=provider.stream,
        )


@pytest.mark.asyncio
async def test_context_transform_and_custom_message_conversion_run_at_provider_boundary(
    model: Model,
) -> None:
    custom = {"role": "audit", "content": "not for the model"}
    context = AgentContext(messages=[UserMessage("old"), assistant_text("old response"), custom])
    observed_transforms: list[list[str]] = []

    async def transform(messages: list[Any], _signal: Any) -> list[Any]:
        observed_transforms.append(
            [
                message.get("role") if isinstance(message, dict) else message.role
                for message in messages
            ]
        )
        return messages[-2:]

    async def convert(messages: list[Any]) -> list[Any]:
        return [message for message in messages if not isinstance(message, dict)]

    provider = FauxProvider([assistant_text("done")])
    result = await agent_loop(
        [UserMessage("new")],
        context,
        AgentLoopConfig(model=model, convert_to_llm=convert, transform_context=transform),
        stream_fn=provider.stream,
    ).result()

    assert observed_transforms == [["user", "assistant", "audit", "user"]]
    assert [message.role for message in provider.requests[0].context.messages] == ["user"]
    assert [
        message.role if not isinstance(message, dict) else message["role"] for message in result
    ] == [
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_tool_progress_and_provider_failure_have_terminal_agent_events(model: Model) -> None:
    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        update: Any,
    ) -> AgentToolResult:
        update(AgentToolResult(content=[TextContent("half")], details={"progress": 0.5}))
        return AgentToolResult(content=[TextContent("done")])

    tool = AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider([assistant_tool("work", {}), assistant_text("finished")])
    events, _ = await collect(
        agent_loop(
            [UserMessage("go")],
            AgentContext(tools=[tool]),
            config(model),
            stream_fn=provider.stream,
        )
    )
    updates = [event for event in events if isinstance(event, ToolExecutionUpdateEvent)]
    assert len(updates) == 1
    assert updates[0].partial_result.details == {"progress": 0.5}

    failed = FauxProvider([RuntimeError("provider down")])
    failed_events, failed_result = await collect(
        agent_loop(
            [UserMessage("go")],
            AgentContext(),
            config(model),
            stream_fn=failed.stream,
        )
    )
    assert failed_events[-1].type == "agent_end"
    assert isinstance(failed_result[-1], AssistantMessage)
    assert failed_result[-1].stop_reason == "error"
    assert failed_result[-1].error_message == "provider down"


@pytest.mark.asyncio
async def test_serial_tool_results_emit_immediately_and_terminate_requires_every_result(
    model: Model,
) -> None:
    async def execute(
        _call_id: str,
        arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        return AgentToolResult(
            content=[TextContent(str(arguments["name"]))],
            terminate=bool(arguments["terminate"]),
        )

    tool = AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={
            "type": "object",
            "required": ["name", "terminate"],
            "properties": {
                "name": {"type": "string"},
                "terminate": {"type": "boolean"},
            },
        },
        execute=execute,
    )
    provider = FauxProvider(
        [
            AssistantMessage(
                content=[
                    ToolCall("call-a", "work", {"name": "a", "terminate": True}),
                    ToolCall("call-b", "work", {"name": "b", "terminate": False}),
                ],
                stop_reason="toolUse",
            ),
            assistant_text("continued because not every tool terminated"),
        ]
    )
    events, result = await collect(
        agent_loop(
            [UserMessage("go")],
            AgentContext(tools=[tool]),
            config(model),
            stream_fn=provider.stream,
        )
    )

    observed: list[tuple[str, str | None]] = []
    for event in events:
        if event.type in {"tool_execution_start", "tool_execution_end"}:
            observed.append((event.type, event.tool_call_id))
        elif event.type in {"message_start", "message_end"} and isinstance(
            event.message, ToolResultMessage
        ):
            observed.append((event.type, event.message.tool_call_id))
    assert observed == [
        ("tool_execution_start", "call-a"),
        ("tool_execution_end", "call-a"),
        ("message_start", "call-a"),
        ("message_end", "call-a"),
        ("tool_execution_start", "call-b"),
        ("tool_execution_end", "call-b"),
        ("message_start", "call-b"),
        ("message_end", "call-b"),
    ]
    assert len(provider.requests) == 2
    assert isinstance(result[-1], AssistantMessage)
    assert result[-1].content == [TextContent("continued because not every tool terminated")]


@pytest.mark.asyncio
async def test_all_terminating_tool_results_stop_the_batch(model: Model) -> None:
    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        return AgentToolResult(content=[TextContent("stop")], terminate=True)

    tool = AgentTool(
        name="stop",
        label="Stop",
        description="Stop",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider(
        [
            AssistantMessage(
                content=[ToolCall("a", "stop", {}), ToolCall("b", "stop", {})],
                stop_reason="toolUse",
            )
        ]
    )
    result = await agent_loop(
        [UserMessage("stop")],
        AgentContext(tools=[tool]),
        config(model),
        stream_fn=provider.stream,
    ).result()

    assert len(provider.requests) == 1
    assert [message.role for message in result] == [
        "user",
        "assistant",
        "toolResult",
        "toolResult",
    ]


@pytest.mark.asyncio
async def test_refreshed_api_key_overrides_static_key(model: Model) -> None:
    provider = FauxProvider([assistant_text("done")])

    async def get_api_key(provider_name: str) -> str:
        assert provider_name == model.provider
        return "fresh"

    await agent_loop(
        [UserMessage("go")],
        AgentContext(),
        AgentLoopConfig(
            model=model,
            convert_to_llm=default_convert_to_llm,
            get_api_key=get_api_key,
            stream_options=StreamOptions(api_key="static"),
        ),
        stream_fn=provider.stream,
    ).result()

    assert provider.requests[0].options.api_key == "fresh"


@pytest.mark.asyncio
async def test_pre_cancelled_run_keeps_lifecycle_and_prompt_in_result(model: Model) -> None:
    token = CancellationToken()
    token.cancel("cancelled before start")
    provider = FauxProvider([assistant_text("must not run")])
    context = AgentContext()
    events, result = await collect(
        agent_loop(
            [UserMessage("go")],
            context,
            config(model),
            stream_fn=provider.stream,
            signal=token,
        )
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_end",
        "turn_end",
        "agent_end",
    ]
    assert [message.role for message in result] == ["user", "assistant"]
    assert isinstance(result[-1], AssistantMessage)
    assert result[-1].stop_reason == "aborted"
    assert result[-1].error_message == "cancelled before start"
    assert provider.requests == []
    assert context.messages == []
