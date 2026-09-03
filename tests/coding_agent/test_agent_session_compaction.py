from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from pi_agent.agent import AgentTool, AgentToolResult
from pi_agent.ai import (
    AssistantMessage,
    CancellationToken,
    FauxProvider,
    Model,
    TextContent,
    ToolCall,
    Usage,
    UserMessage,
)
from pi_agent.coding_agent import (
    CompactionEndEvent,
    CompactionStartEvent,
    CreateAgentSessionOptions,
    SettingsResolver,
    create_agent_session,
)
from pi_agent.harness.compaction import SummaryResponse


def settings(tmp_path: Path, *, reserve: int = 100, keep: int = 150):
    return SettingsResolver().resolve(
        environ={},
        runtime={
            "session.enabled": False,
            "resources.user_root": str(tmp_path / "resources"),
            "compaction.enabled": True,
            "compaction.reserve_tokens": reserve,
            "compaction.keep_recent_tokens": keep,
        },
    )


@pytest.mark.asyncio
async def test_large_tool_result_is_compacted_before_next_provider_request(
    model: Model,
    tmp_path: Path,
) -> None:
    small_model = replace(model, context_window=600, max_tokens=200)

    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        return AgentToolResult(content=[TextContent("x" * 4_000)])

    async def summarize(
        _conversation: str,
        _previous_summary: str | None = None,
    ) -> SummaryResponse:
        return SummaryResponse("preserved summary", Usage(input=10, output=3, total_tokens=13))

    tool = AgentTool(
        name="huge",
        label="Huge",
        description="Return a large result",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider(
        [
            AssistantMessage(
                content=[ToolCall("tool-1", "huge", {})],
                stop_reason="toolUse",
            ),
            AssistantMessage(content=[TextContent("done")], stop_reason="stop"),
        ]
    )
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=small_model,
                stream_fn=provider.stream,
                settings=settings(tmp_path),
                tools=[tool],
                include_coding_tools=False,
                summarizer=summarize,
            )
        )
    ).session
    events: list[Any] = []
    session.subscribe(events.append)

    await session.prompt("run the large tool")

    assert len(provider.requests) == 2
    second_messages = provider.requests[1].context.messages
    assert len(second_messages) == 1
    assert isinstance(second_messages[0], UserMessage)
    assert "preserved summary" in str(second_messages[0].content)
    assert [event.type for event in events].count("compaction_start") == 1
    assert [event.type for event in events].count("compaction_end") == 1
    assert any(isinstance(event, CompactionStartEvent) for event in events)
    end = next(event for event in events if isinstance(event, CompactionEndEvent))
    assert end.result is not None and end.result.tokens_after < end.result.tokens_before
    assert any(entry["type"] == "compaction" for entry in session.get_entries())


@pytest.mark.asyncio
async def test_under_threshold_prompts_do_not_emit_compaction_events(
    model: Model,
    tmp_path: Path,
) -> None:
    async def summarize(_conversation: str, _previous_summary: str | None = None) -> str:
        return "unused"

    provider = FauxProvider(
        [
            AssistantMessage(content=[TextContent("first")], stop_reason="stop"),
            AssistantMessage(content=[TextContent("second")], stop_reason="stop"),
        ]
    )
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model,
                stream_fn=provider.stream,
                settings=settings(tmp_path, reserve=100, keep=200),
                include_coding_tools=False,
                summarizer=summarize,
            )
        )
    ).session
    events: list[Any] = []
    session.subscribe(events.append)

    await session.prompt("one")
    await session.prompt("two")

    assert not any(isinstance(event, CompactionStartEvent | CompactionEndEvent) for event in events)
