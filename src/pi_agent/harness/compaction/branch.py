from __future__ import annotations

import inspect
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TypeVar

from pi_agent.ai import Message, Usage
from pi_agent.harness.session import (
    BranchSummaryEntry,
    MessageEntry,
    SessionManager,
    SessionTree,
    TreeEntry,
)

from .compaction import serialize_conversation
from .types import Summarizer, SummaryResponse

T = TypeVar("T")


@dataclass(slots=True, frozen=True)
class BranchPreparation:
    common_ancestor_id: str | None
    from_entry_id: str
    to_entry_id: str
    entries: tuple[TreeEntry, ...]
    messages: tuple[Message, ...]


@dataclass(slots=True, frozen=True)
class BranchSummaryResult:
    summary: str
    usage: Usage
    preparation: BranchPreparation


def collect_entries_for_branch_summary(
    tree: SessionTree,
    *,
    from_entry_id: str,
    to_entry_id: str,
) -> BranchPreparation:
    tree.get(from_entry_id)
    tree.get(to_entry_id)
    common = tree.common_ancestor(from_entry_id, to_entry_id)
    source_path = list(tree.path_to(from_entry_id).entries)
    start = 0
    if common is not None:
        start = next(index for index, entry in enumerate(source_path) if entry.id == common) + 1
    entries = tuple(source_path[start:])
    messages = tuple(entry.message for entry in entries if isinstance(entry, MessageEntry))
    return BranchPreparation(common, from_entry_id, to_entry_id, entries, messages)


async def generate_branch_summary(
    preparation: BranchPreparation,
    summarizer: Summarizer,
) -> BranchSummaryResult:
    if not preparation.messages:
        return BranchSummaryResult("", Usage.zero(), preparation)
    raw = await _maybe_await(summarizer(serialize_conversation(preparation.messages), None))
    response = raw if isinstance(raw, SummaryResponse) else SummaryResponse(str(raw))
    return BranchSummaryResult(response.text.strip(), response.usage, preparation)


async def summarize_and_navigate_branch(
    manager: SessionManager,
    *,
    to_entry_id: str,
    summarizer: Summarizer,
) -> tuple[BranchSummaryEntry | None, BranchSummaryResult]:
    if manager.leaf_id is None:
        raise ValueError("cannot summarize an empty session branch")
    preparation = collect_entries_for_branch_summary(
        manager.tree,
        from_entry_id=manager.leaf_id,
        to_entry_id=to_entry_id,
    )
    result = await generate_branch_summary(preparation, summarizer)
    manager.navigate(to_entry_id)
    if not result.summary:
        return None, result
    entry = manager.append_branch_summary(
        result.summary,
        from_entry_id=preparation.from_entry_id,
        to_entry_id=preparation.to_entry_id,
        details={
            "commonAncestorId": preparation.common_ancestor_id,
            "usage": {
                "input": result.usage.input,
                "output": result.usage.output,
                "totalTokens": result.usage.total_tokens,
            },
        },
    )
    return entry, result


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value
