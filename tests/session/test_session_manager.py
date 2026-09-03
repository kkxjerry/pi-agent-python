from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_agent.ai import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from pi_agent.harness.session import SessionCorruptionError, SessionManager


@pytest.mark.asyncio
async def test_jsonl_session_round_trip_branch_and_resume(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    manager = await SessionManager.create(path, cwd=tmp_path, name="test")
    user = await manager.append_message(UserMessage("start", timestamp=1))
    assistant = await manager.append_message(
        AssistantMessage(
            content=[ToolCall("call-1", "read", {"path": "README.md"})],
            stop_reason="toolUse",
            timestamp=2,
        )
    )
    await manager.append_message(
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="read",
            content=[TextContent("contents")],
            timestamp=3,
        )
    )
    first_leaf = manager.current_id

    await manager.navigate(user.id)
    alternate = await manager.append_message(
        AssistantMessage(content=[TextContent("alternate")], stop_reason="stop", timestamp=4)
    )

    assert manager.current_id == alternate.id
    assert [message.role for message in manager.model_context()] == ["user", "assistant"]
    assert manager.common_ancestor(first_leaf, alternate.id) == user.id
    assert len(manager.tree()) == 1
    assert len(manager.tree()[0].children) == 2

    reopened = await SessionManager.open(path)
    assert reopened.current_id == alternate.id
    assert reopened.info.name == "test"
    assert [message.role for message in reopened.model_context()] == ["user", "assistant"]
    assert assistant.id in reopened.entry_map


@pytest.mark.asyncio
async def test_valid_final_record_without_newline_remains_appendable(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    manager = await SessionManager.create(path, cwd=tmp_path)
    await manager.append_message(UserMessage("one", timestamp=1))
    path.write_bytes(path.read_bytes().rstrip(b"\n"))

    reopened = await SessionManager.open(path)
    await reopened.append_message(
        AssistantMessage(content=[TextContent("two")], stop_reason="stop", timestamp=2)
    )

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [record["type"] for record in records] == ["session", "message", "message"]


@pytest.mark.asyncio
async def test_corrupt_tail_requires_explicit_repair(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    manager = await SessionManager.create(path, cwd=tmp_path)
    await manager.append_message(UserMessage("ok", timestamp=1))
    with path.open("ab") as handle:
        handle.write(b'{"type":"message"')

    with pytest.raises(SessionCorruptionError):
        await SessionManager.open(path)

    repaired = await SessionManager.open(path, repair_tail=True)
    assert [message.role for message in repaired.model_context()] == ["user"]
    assert path.read_bytes().endswith(b"\n")


@pytest.mark.asyncio
async def test_orphan_tool_result_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    manager = await SessionManager.create(path, cwd=tmp_path)
    with pytest.raises(SessionCorruptionError, match="orphan ToolResult"):
        await manager.append_message(
            ToolResultMessage(
                tool_call_id="missing",
                tool_name="read",
                content=[TextContent("bad")],
            )
        )
