from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.ai import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from pi_agent.harness.compaction import (
    BeforeCompactionDecision,
    CompactionCancelled,
    CompactionSettings,
    SummaryResponse,
    collect_entries_for_branch_summary,
    collect_file_operations,
    compact,
    compact_session,
    find_cut_point,
    prepare_compaction,
    safe_cut_indices,
    should_compact,
    summarize_and_navigate_branch,
)
from pi_agent.harness.session import BranchSummaryEntry, SessionManager, reconstruct_context


def _text(value: str) -> UserMessage:
    return UserMessage(value)


def test_safe_cut_never_splits_tool_call_and_result() -> None:
    messages = [
        _text("start"),
        AssistantMessage(
            content=[ToolCall("call-1", "read", {"path": "README.md"})],
            stop_reason="toolUse",
        ),
        ToolResultMessage("call-1", "read", [TextContent("contents")]),
        AssistantMessage(content=[TextContent("done")], stop_reason="stop"),
        _text("next"),
    ]
    safe = safe_cut_indices(messages)
    assert 2 not in safe
    assert 3 in safe
    cut, _split = find_cut_point(messages, keep_recent_tokens=8)
    assert cut != 2


def test_prepare_compaction_requires_threshold_and_tracks_split_turn() -> None:
    messages = [_text("a" * 80), AssistantMessage(content=[TextContent("b" * 80)])]
    settings = CompactionSettings(context_window=30, reserve_tokens=5, keep_recent_tokens=10)
    assert should_compact(messages, settings)
    preparation = prepare_compaction(messages, settings)
    assert preparation.cut_index == 1
    assert preparation.split_turn is True
    assert preparation.messages_to_keep == (messages[1],)


def test_reported_assistant_usage_drives_compaction_gate() -> None:
    assistant = AssistantMessage(
        content=[TextContent("small")],
        usage=Usage(input=80, output=10, total_tokens=90),
        stop_reason="stop",
    )
    settings = CompactionSettings(context_window=100, reserve_tokens=20, keep_recent_tokens=10)
    assert should_compact([_text("small"), assistant], settings)


@pytest.mark.asyncio
async def test_compact_retries_and_preserves_usage_and_hooks() -> None:
    messages = [_text("old " * 50), _text("keep")]
    settings = CompactionSettings(
        context_window=40, reserve_tokens=5, keep_recent_tokens=8, max_attempts=2
    )
    calls = 0
    hook_order: list[str] = []

    async def summarizer(_conversation: str, previous: str | None) -> SummaryResponse:
        nonlocal calls
        calls += 1
        assert previous == "previous"
        if calls == 1:
            raise RuntimeError("transient")
        return SummaryResponse("summary", Usage(input=4, output=2, total_tokens=6))

    async def before(preparation):
        hook_order.append("before")
        return BeforeCompactionDecision(preparation=preparation)

    async def after(result):
        hook_order.append("after")
        return result

    result = await compact(
        messages,
        settings,
        summarizer,
        previous_summary="previous",
        force=True,
        before=before,
        after=after,
    )
    assert calls == 2
    assert result.attempts == 2
    assert result.summary == "summary"
    assert result.usage.total_tokens == 6
    assert result.active_messages[0].content.endswith("summary")
    assert hook_order == ["before", "after"]


@pytest.mark.asyncio
async def test_before_hook_can_cancel_compaction() -> None:
    async def before(_preparation):
        return BeforeCompactionDecision(cancel=True)

    with pytest.raises(CompactionCancelled):
        await compact(
            [_text("old" * 30), _text("new")],
            CompactionSettings(context_window=20, reserve_tokens=5, keep_recent_tokens=5),
            lambda *_args: "never",
            force=True,
            before=before,
        )


def test_file_operation_tracking_is_deterministic() -> None:
    messages = [
        AssistantMessage(
            content=[
                ToolCall("r", "read", {"path": "a.py"}),
                ToolCall("w", "edit", {"path": "b.py"}),
            ]
        ),
        ToolResultMessage(
            "w",
            "edit",
            [TextContent("ok")],
            details={"files": ["c.py", "b.py"], "operation": "edit"},
        ),
    ]
    operations = collect_file_operations(messages)
    assert operations.read == ("a.py",)
    assert operations.modified == ("b.py", "c.py")


@pytest.mark.asyncio
async def test_compact_session_appends_entry_and_reconstructs_active_context(
    tmp_path: Path,
) -> None:
    manager = SessionManager.create(tmp_path / "s.jsonl", cwd="/repo", session_id="s")
    manager.append_message(_text("old " * 100), entry_id="m1")
    manager.append_message(_text("keep"), entry_id="m2")

    entry, result = await compact_session(
        manager,
        CompactionSettings(context_window=50, reserve_tokens=5, keep_recent_tokens=8),
        lambda *_args: SummaryResponse("state summary", Usage(input=2, output=1)),
        force=True,
    )
    assert entry.summary == "state summary"
    assert entry.first_kept_entry_id == "m2"
    assert result.cut_index == 1
    reconstructed = reconstruct_context(manager.tree.active_path())
    assert reconstructed.messages[0].content.endswith("state summary")
    assert reconstructed.messages[1].content == "keep"


@pytest.mark.asyncio
async def test_branch_summary_covers_only_abandoned_branch(tmp_path: Path) -> None:
    manager = SessionManager.create(tmp_path / "s.jsonl", cwd="/repo", session_id="s")
    common = manager.append_message(_text("common"), entry_id="m1")
    old = manager.append_message(_text("old branch"), entry_id="m2")
    manager.fork(common.id)
    new = manager.append_message(_text("new branch"), entry_id="m3")
    manager.navigate(old.id)

    preparation = collect_entries_for_branch_summary(
        manager.tree, from_entry_id=old.id, to_entry_id=new.id
    )
    assert [message.content for message in preparation.messages] == ["old branch"]

    seen: list[str] = []

    async def summarizer(conversation: str, _previous: str | None) -> str:
        seen.append(conversation)
        return "abandoned work"

    entry, result = await summarize_and_navigate_branch(
        manager, to_entry_id=new.id, summarizer=summarizer
    )
    assert isinstance(entry, BranchSummaryEntry)
    assert result.summary == "abandoned work"
    assert "old branch" in seen[0]
    assert manager.tree.path_to(manager.leaf_id).entries[-1] == entry
