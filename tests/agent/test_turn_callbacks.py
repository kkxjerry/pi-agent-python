from __future__ import annotations

from typing import Any

import pytest

from pi_agent.agent import (
    AgentContext,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentTool,
    AgentToolResult,
    CompletedTurnContext,
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
    UserMessage,
)


def tool_response() -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall("tool-1", "work", {})],
        stop_reason="toolUse",
    )


def work_tool() -> AgentTool:
    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        return AgentToolResult(content=[TextContent("worked")])

    return AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
    )


@pytest.mark.asyncio
async def test_should_stop_after_turn_exits_after_completed_tool_batch(model: Model) -> None:
    observed: list[CompletedTurnContext] = []

    async def should_stop(context: CompletedTurnContext) -> bool:
        observed.append(context)
        return True

    provider = FauxProvider([tool_response()])
    result = await agent_loop(
        [UserMessage("go")],
        AgentContext(tools=[work_tool()]),
        AgentLoopConfig(
            model=model,
            convert_to_llm=default_convert_to_llm,
            should_stop_after_turn=should_stop,
        ),
        stream_fn=provider.stream,
    ).result()

    assert len(provider.requests) == 1
    assert len(observed) == 1
    assert [message.role for message in result] == ["user", "assistant", "toolResult"]
    assert observed[0].tool_results[0].tool_call_id == "tool-1"


@pytest.mark.asyncio
async def test_prepare_next_turn_can_replace_runtime_context(model: Model) -> None:
    tool = work_tool()

    async def prepare(context: CompletedTurnContext) -> AgentLoopTurnUpdate:
        return AgentLoopTurnUpdate(
            context=AgentContext(
                system_prompt="next-system",
                messages=list(context.context.messages),
                tools=context.context.tools,
            )
        )

    provider = FauxProvider(
        [
            tool_response(),
            AssistantMessage(content=[TextContent("done")], stop_reason="stop"),
        ]
    )
    await agent_loop(
        [UserMessage("go")],
        AgentContext(system_prompt="first-system", tools=[tool]),
        AgentLoopConfig(
            model=model,
            convert_to_llm=default_convert_to_llm,
            prepare_next_turn=prepare,
        ),
        stream_fn=provider.stream,
    ).result()

    assert provider.requests[0].context.system_prompt == "first-system"
    assert provider.requests[1].context.system_prompt == "next-system"
    assert [message.role for message in provider.requests[1].context.messages] == [
        "user",
        "assistant",
        "toolResult",
    ]
