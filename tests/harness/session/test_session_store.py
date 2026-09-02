from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_agent.ai import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from pi_agent.harness.session import (
    CursorEntry,
    JsonlSessionStore,
    MessageEntry,
    SessionCorruptionError,
    SessionHeader,
    SessionManager,
    SessionRepository,
    SessionValidationError,
    decode_jsonl,
    reconstruct_context,
    record_to_dict,
    validate_records,
)


def test_jsonl_store_round_trip_and_missing_final_newline_append(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    header = SessionHeader("session-1", "/repo", timestamp=1)
    path.write_text(json.dumps(record_to_dict(header), separators=(",", ":")), encoding="utf-8")
    store = JsonlSessionStore(path)
    store.append(MessageEntry("m1", None, UserMessage("hello", timestamp=2), timestamp=2))

    raw = path.read_bytes()
    assert raw.count(b"\n") == 2
    loaded = store.load()
    assert loaded.header == header
    assert isinstance(loaded.records[1], MessageEntry)
    assert loaded.records[1].message == UserMessage("hello", timestamp=2)


def test_corrupt_unterminated_tail_is_recoverable_but_middle_corruption_is_not(
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    store = JsonlSessionStore.create(path, SessionHeader("session-1", "/repo", timestamp=1))
    store.append(MessageEntry("m1", None, UserMessage("ok", timestamp=2), timestamp=2))
    path.write_bytes(path.read_bytes() + b'{"type":"message"')

    loaded = store.load()
    assert [record.type for record in loaded.records] == ["session", "message"]
    assert loaded.ignored_tail
    assert loaded.warnings[0].code == "corrupt_tail"
    with pytest.raises(SessionCorruptionError, match="repair"):
        store.append(CursorEntry("m1", timestamp=3))

    repaired = store.repair_tail()
    assert repaired.ignored_tail
    store.append(CursorEntry("m1", timestamp=4))
    assert store.load().records[-1] == CursorEntry("m1", timestamp=4)

    path.write_bytes(path.read_bytes() + b"not-json\n")
    with pytest.raises(SessionCorruptionError, match="invalid session record"):
        store.load()


def test_validation_rejects_duplicate_parent_and_orphan_tool_result() -> None:
    header = SessionHeader("s", "/repo", timestamp=1)
    user = MessageEntry("u", None, UserMessage("go", timestamp=2), timestamp=2)
    with pytest.raises(SessionValidationError, match="duplicate entry"):
        validate_records([header, user, user])
    with pytest.raises(SessionValidationError, match="unknown parent"):
        validate_records(
            [header, MessageEntry("m", "missing", UserMessage("x", timestamp=3), timestamp=3)]
        )
    with pytest.raises(SessionValidationError, match="no ancestor tool call"):
        validate_records(
            [
                header,
                user,
                MessageEntry(
                    "r",
                    "u",
                    ToolResultMessage("call-1", "read", [TextContent("x")], timestamp=3),
                    timestamp=3,
                ),
            ]
        )


def test_session_manager_branches_without_deleting_old_history(tmp_path: Path) -> None:
    manager = SessionManager.create(tmp_path / "s.jsonl", cwd="/repo", session_id="s")
    first = manager.append_message(UserMessage("first", timestamp=1), entry_id="m1")
    old_leaf = manager.append_message(UserMessage("old branch", timestamp=2), entry_id="m2")

    manager.fork(first.id)
    new_leaf = manager.append_message(UserMessage("new branch", timestamp=3), entry_id="m3")

    assert set(manager.tree.leaf_ids()) == {old_leaf.id, new_leaf.id}
    assert [message.content for message in manager.active_messages()] == ["first", "new branch"]
    assert manager.tree.common_ancestor(old_leaf.id, new_leaf.id) == first.id
    assert [entry.id for entry in manager.tree.branch_delta(old_leaf.id, new_leaf.id)] == ["m3"]


def test_repository_list_rename_export_import_delete(tmp_path: Path) -> None:
    repository = SessionRepository(tmp_path / "sessions")
    manager = repository.create(cwd="/repo", session_id="s1", name="initial")
    manager.append_message(UserMessage("hello"), entry_id="m1")
    repository.rename("s1", "renamed")

    listed = repository.list()
    assert len(listed) == 1
    assert listed[0].name == "renamed"
    exported = repository.export("s1", tmp_path / "exported.jsonl")
    imported = repository.import_file(exported, session_id="s2")
    assert imported.header.id == "s2"
    assert imported.header.parent_session_id == "s1"
    assert [message.content for message in imported.active_messages()] == ["hello"]

    repository.delete("s1")
    assert [item.id for item in repository.list()] == ["s2"]


def test_reconstruct_context_uses_latest_compaction_and_kept_tail(tmp_path: Path) -> None:
    manager = SessionManager.create(tmp_path / "s.jsonl", cwd="/repo", session_id="s")
    manager.append_message(UserMessage("old", timestamp=1), entry_id="m1")
    manager.append_message(UserMessage("keep", timestamp=2), entry_id="m2")
    compact = manager.append_compaction(
        "old work summary",
        first_kept_entry_id="m2",
        tokens_before=100,
        tokens_after=20,
        entry_id="c1",
    )
    manager.append_message(UserMessage("latest", timestamp=3), entry_id="m3")

    reconstructed = reconstruct_context(manager.tree.active_path())
    assert reconstructed.compaction == compact
    assert reconstructed.messages[0].content.endswith("old work summary")
    assert [message.content for message in reconstructed.messages[1:]] == ["keep", "latest"]


def test_unresolved_tool_call_is_warning_and_can_be_completed(tmp_path: Path) -> None:
    manager = SessionManager.create(tmp_path / "s.jsonl", cwd="/repo", session_id="s")
    assistant = AssistantMessage(
        content=[ToolCall("call-1", "read", {"path": "README.md"})],
        stop_reason="toolUse",
        timestamp=1,
    )
    manager.append_message(assistant, entry_id="a1")
    assert any("call-1" in warning for warning in manager.warnings)
    manager.append_message(
        ToolResultMessage("call-1", "read", [TextContent("contents")], timestamp=2),
        entry_id="r1",
    )
    assert not any("call-1" in warning for warning in manager.warnings)


def test_decode_jsonl_accepts_valid_last_record_without_newline() -> None:
    header = SessionHeader("s", "/repo", timestamp=1)
    data = json.dumps(record_to_dict(header), separators=(",", ":")).encode()
    result = decode_jsonl(data)
    assert result.header == header
    assert result.ignored_tail == b""
