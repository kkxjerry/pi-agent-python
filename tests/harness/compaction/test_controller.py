from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.ai import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from pi_agent.harness.compaction import CompactionController, CompactionSettings
from pi_agent.harness.session import SessionManager


@pytest.mark.asyncio
async def test_controller_compacts_large_tool_result_before_next_provider_context(
    tmp_path: Path,
) -> None:
    manager = SessionManager.create(tmp_path / "s.jsonl", cwd="/repo", session_id="s")
    manager.append_message(UserMessage("read the file"), entry_id="u1")
    manager.append_message(
        AssistantMessage(
            content=[ToolCall("call-1", "read", {"path": "large.txt"})],
            stop_reason="toolUse",
        ),
        entry_id="a1",
    )
    manager.append_message(
        ToolResultMessage(
            "call-1",
            "read",
            [TextContent("x" * 1_000)],
            details={"path": "large.txt", "operation": "read"},
        ),
        entry_id="r1",
    )
    controller = CompactionController(
        CompactionSettings(context_window=100, reserve_tokens=20, keep_recent_tokens=20),
        lambda *_args: "large tool result summarized",
    )

    decision = await controller.prepare_next_provider_context(manager)

    assert decision.compacted is True
    assert decision.entry is not None
    assert decision.entry.first_kept_entry_id is None
    assert decision.result is not None and decision.result.split_turn is True
    assert len(decision.messages) == 1
    assert decision.messages[0].content.endswith("large tool result summarized")


@pytest.mark.asyncio
async def test_controller_is_noop_below_threshold(tmp_path: Path) -> None:
    manager = SessionManager.create(tmp_path / "s.jsonl", cwd="/repo", session_id="s")
    manager.append_message(UserMessage("small"), entry_id="u1")
    controller = CompactionController(
        CompactionSettings(context_window=1_000, reserve_tokens=100, keep_recent_tokens=200),
        lambda *_args: "unused",
    )

    decision = await controller.prepare_next_provider_context(manager)

    assert decision.compacted is False
    assert [message.content for message in decision.messages] == ["small"]
