from __future__ import annotations

from typing import Any

import pytest

from pi_agent.ai import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from pi_agent.harness import (
    CompactionController,
    CompactionSettings,
    SummaryResult,
    compact,
    prepare_compaction,
)


def messages() -> list[Any]:
    return [
        UserMessage("inspect repository", timestamp=1),
        AssistantMessage(
            content=[ToolCall("call-1", "read", {"path": "a.py"})],
            stop_reason="toolUse",
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="read",
            content=[TextContent("x" * 400)],
            timestamp=3,
        ),
        UserMessage("fix it", timestamp=4),
        AssistantMessage(content=[TextContent("done")], stop_reason="stop", timestamp=5),
    ]


def test_compaction_cut_never_splits_tool_call_and_result() -> None:
    preparation = prepare_compaction(
        messages(),
        context_window=100,
        settings=CompactionSettings(reserve_tokens=10, keep_recent_tokens=20),
    )

    summarized_roles = [message.role for message in preparation.summarized]
    retained_roles = [message.role for message in preparation.retained]
    assert not (summarized_roles[-1:] == ["assistant"] and retained_roles[:1] == ["toolResult"])
    assert preparation.files.read == ("a.py",)


@pytest.mark.asyncio
async def test_compact_builds_summary_context_and_tracks_usage() -> None:
    seen: list[list[str]] = []

    async def summarizer(items: list[Any], previous: str | None, _signal: Any) -> SummaryResult:
        seen.append([item.role for item in items])
        assert previous == "older"
        return SummaryResult("compact summary")

    result = await compact(
        messages(),
        context_window=100,
        settings=CompactionSettings(reserve_tokens=10, keep_recent_tokens=20),
        summarizer=summarizer,
        previous_summary="older",
    )

    assert seen
    assert isinstance(result.messages[0], UserMessage)
    assert "compact summary" in result.messages[0].content[0].text
    assert result.summarized_count > 0
    assert result.estimated_tokens_after > 0


@pytest.mark.asyncio
async def test_controller_skips_small_context_and_compacts_when_forced(model: Any) -> None:
    async def summarizer(_items: list[Any], _previous: str | None, _signal: Any) -> str:
        return "summary"

    controller = CompactionController(
        CompactionSettings(reserve_tokens=1, keep_recent_tokens=1),
        summarizer,
    )
    small = [UserMessage("hello")]
    assert controller.needed(small, model) is False
    result = await controller.prepare_next_provider_context(small, model, force=True)
    assert len(result) == 1
    assert "summary" in result[0].content[0].text
    assert controller.previous_summary == "summary"
