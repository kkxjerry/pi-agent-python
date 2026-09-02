from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from pi_agent.agent import (
    AfterToolCallResult,
    AgentContext,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentTool,
    AgentToolResult,
    MessageStartEvent,
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


def assistant_tool() -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall("tool-1", "work", {})],
        stop_reason="toolUse",
    )


def config(model: Model, **kwargs: Any) -> AgentLoopConfig:
    return AgentLoopConfig(model=model, convert_to_llm=default_convert_to_llm, **kwargs)


def work_tool() -> AgentTool:
    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        return AgentToolResult(
            content=[TextContent("worked")],
            details={"keep": True},
        )

    return AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
    )


@pytest.mark.asyncio
async def test_initial_steering_is_injected_before_first_provider_request(model: Model) -> None:
    delivered = False

    async def steering() -> list[UserMessage]:
        nonlocal delivered
        if delivered:
            return []
        delivered = True
        return [UserMessage("steer-first")]

    provider = FauxProvider([assistant_text("done")])
    events = [
        event
        async for event in agent_loop(
            [UserMessage("prompt")],
            AgentContext(),
            config(model, get_steering_messages=steering),
            stream_fn=provider.stream,
        )
    ]

    assert [message.content for message in provider.requests[0].context.messages] == [
        "prompt",
        "steer-first",
    ]
    user_starts = [
        event.message.content
        for event in events
        if isinstance(event, MessageStartEvent) and isinstance(event.message, UserMessage)
    ]
    assert user_starts == ["prompt", "steer-first"]


@pytest.mark.asyncio
async def test_should_stop_after_turn_precedes_steering_and_follow_up(model: Model) -> None:
    steering_calls = 0
    follow_up_calls = 0

    async def steering() -> list[UserMessage]:
        nonlocal steering_calls
        steering_calls += 1
        return []

    async def follow_up() -> list[UserMessage]:
        nonlocal follow_up_calls
        follow_up_calls += 1
        return [UserMessage("must not run")]

    provider = FauxProvider([assistant_tool(), assistant_text("unused")])
    result = await agent_loop(
        [UserMessage("go")],
        AgentContext(tools=[work_tool()]),
        config(
            model,
            get_steering_messages=steering,
            get_follow_up_messages=follow_up,
            should_stop_after_turn=lambda _context: True,
        ),
        stream_fn=provider.stream,
    ).result()

    assert len(provider.requests) == 1
    assert steering_calls == 1  # initial poll only; no post-turn poll
    assert follow_up_calls == 0
    assert [message.role for message in result] == ["user", "assistant", "toolResult"]


@pytest.mark.asyncio
async def test_prepare_next_turn_can_replace_context_model_and_thinking(model: Model) -> None:
    next_model = replace(model, id="next-model", name="next-model")
    provider = FauxProvider([assistant_tool(), assistant_text("done")])
    prepare_calls = 0

    async def prepare(context: Any) -> AgentLoopTurnUpdate:
        nonlocal prepare_calls
        prepare_calls += 1
        return AgentLoopTurnUpdate(
            context=AgentContext(
                system_prompt="next-system",
                messages=context.context.messages,
                tools=context.context.tools,
            ),
            model=next_model,
            thinking_level="high",
        )

    await agent_loop(
        [UserMessage("go")],
        AgentContext(system_prompt="initial", tools=[work_tool()]),
        config(model, prepare_next_turn=prepare),
        stream_fn=provider.stream,
    ).result()

    assert prepare_calls == 1
    assert [request.model.id for request in provider.requests] == [model.id, "next-model"]
    assert [request.context.system_prompt for request in provider.requests] == [
        "initial",
        "next-system",
    ]
    assert provider.requests[1].options.reasoning == "high"


@pytest.mark.asyncio
async def test_after_tool_none_values_do_not_override_existing_fields(model: Model) -> None:
    async def after(_context: Any, _signal: Any) -> AfterToolCallResult:
        return AfterToolCallResult(details=None, usage=None)

    provider = FauxProvider([assistant_tool(), assistant_text("done")])
    result = await agent_loop(
        [UserMessage("go")],
        AgentContext(tools=[work_tool()]),
        config(model, after_tool_call=after),
        stream_fn=provider.stream,
    ).result()

    tool_result = next(message for message in result if isinstance(message, ToolResultMessage))
    assert tool_result.details == {"keep": True}
