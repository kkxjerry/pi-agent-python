from __future__ import annotations

from typing import Any

from pi_agent.ai import Message, UserMessage

from .types import SessionEntry


def active_branch(entries: list[SessionEntry], current_id: str | None) -> list[SessionEntry]:
    if current_id is None:
        return []
    by_id = {entry.id: entry for entry in entries if entry.type != "cursor"}
    branch: list[SessionEntry] = []
    cursor = current_id
    seen: set[str] = set()
    while cursor is not None:
        if cursor in seen:
            raise ValueError(f"session branch contains a cycle at {cursor!r}")
        seen.add(cursor)
        entry = by_id.get(cursor)
        if entry is None:
            raise ValueError(f"session branch references unknown entry {cursor!r}")
        branch.append(entry)
        cursor = entry.parent_id
    branch.reverse()
    return branch


def build_model_context(entries: list[SessionEntry], current_id: str | None) -> list[Message]:
    """Build the active model transcript from an append-only branch.

    The latest compaction replaces the summarized prefix with one synthetic user
    message. Original records remain in the JSONL file and can still be inspected
    or used by another branch.
    """

    branch = active_branch(entries, current_id)
    latest_compaction = -1
    for index, entry in enumerate(branch):
        if entry.type == "compaction":
            latest_compaction = index

    messages: list[Message] = []
    start = 0
    if latest_compaction >= 0:
        compaction = branch[latest_compaction]
        summary = compaction.data.get("summary")
        if isinstance(summary, str) and summary:
            messages.append(
                UserMessage(f"<conversation-summary>\n{summary}\n</conversation-summary>")
            )
        first_kept = compaction.data.get("firstKeptId")
        if isinstance(first_kept, str):
            for index, entry in enumerate(branch):
                if entry.id == first_kept:
                    start = index
                    break
            else:
                start = latest_compaction + 1
        else:
            start = latest_compaction + 1

    for entry in branch[start:]:
        if entry.type == "message":
            message = entry.message
            if message is not None:
                messages.append(message)
        elif entry.type == "branch_summary":
            summary = entry.data.get("summary")
            if isinstance(summary, str) and summary:
                messages.append(UserMessage(f"<branch-summary>\n{summary}\n</branch-summary>"))
    return messages


def entry_payload(entry: SessionEntry) -> dict[str, Any]:
    return {"id": entry.id, "parentId": entry.parent_id, "type": entry.type, **entry.data}
