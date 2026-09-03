from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.ai import AssistantMessage, TextContent, ToolResultMessage, UserMessage
from pi_agent.harness.session import SessionManager, SessionValidationError


def test_session_manager_round_trip_and_branch_navigation(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    manager = SessionManager.create(path, cwd=str(tmp_path), name="test")
    user = manager.append_message(UserMessage("start"), entry_id="user")
    first = manager.append_message(
        AssistantMessage(content=[TextContent("first")], stop_reason="stop"),
        entry_id="first",
    )
    manager.navigate(user.id)
    alternate = manager.append_message(
        AssistantMessage(content=[TextContent("alternate")], stop_reason="stop"),
        entry_id="alternate",
    )

    assert manager.leaf_id == alternate.id
    assert manager.tree.common_ancestor(first.id, alternate.id) == user.id
    reopened = SessionManager(manager.store)
    assert reopened.leaf_id == alternate.id
    assert [message.role for message in reopened.active_messages()] == ["user", "assistant"]


def test_orphan_tool_result_is_rejected(tmp_path: Path) -> None:
    manager = SessionManager.create(tmp_path / "session.jsonl", cwd=str(tmp_path))
    with pytest.raises(SessionValidationError, match="no ancestor tool call"):
        manager.append_message(
            ToolResultMessage(
                tool_call_id="missing",
                tool_name="read",
                content=[TextContent("bad")],
            )
        )
