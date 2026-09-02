from __future__ import annotations

from dataclasses import dataclass

from pi_agent.ai import Message, UserMessage

from .tree import SessionPath
from .types import (
    BranchSummaryEntry,
    CompactionEntry,
    MessageEntry,
    ModelChangeEntry,
    ThinkingLevelChangeEntry,
)

DEFAULT_COMPACTION_PREFIX = (
    "The conversation before this point was compacted. Continue from this summary:\n\n"
)
DEFAULT_BRANCH_SUMMARY_PREFIX = (
    "The user explored a different conversation branch before returning here.\n"
    "Summary of that exploration:\n\n"
)


@dataclass(slots=True, frozen=True)
class ReconstructedContext:
    messages: tuple[Message, ...]
    model_provider: str | None
    model_id: str | None
    thinking_level: str | None
    compaction: CompactionEntry | None


def reconstruct_context(
    path: SessionPath,
    *,
    compaction_prefix: str = DEFAULT_COMPACTION_PREFIX,
    branch_summary_prefix: str = DEFAULT_BRANCH_SUMMARY_PREFIX,
) -> ReconstructedContext:
    entries = list(path.entries)
    model_provider: str | None = None
    model_id: str | None = None
    thinking_level: str | None = None
    latest_compaction: CompactionEntry | None = None

    for entry in entries:
        if isinstance(entry, ModelChangeEntry):
            model_provider = entry.provider
            model_id = entry.model_id
        elif isinstance(entry, ThinkingLevelChangeEntry):
            thinking_level = entry.thinking_level
        elif isinstance(entry, CompactionEntry):
            latest_compaction = entry

    start = 0
    messages: list[Message] = []
    if latest_compaction is not None:
        messages.append(UserMessage(compaction_prefix + latest_compaction.summary))
        if latest_compaction.first_kept_entry_id is not None:
            for index, entry in enumerate(entries):
                if entry.id == latest_compaction.first_kept_entry_id:
                    start = index
                    break
            else:
                raise ValueError(
                    "compaction first_kept_entry_id is not on the active session branch: "
                    f"{latest_compaction.first_kept_entry_id}"
                )
        else:
            start = entries.index(latest_compaction) + 1

    for entry in entries[start:]:
        if isinstance(entry, MessageEntry):
            messages.append(entry.message)
        elif isinstance(entry, BranchSummaryEntry):
            messages.append(UserMessage(branch_summary_prefix + entry.summary))
    return ReconstructedContext(
        tuple(messages),
        model_provider,
        model_id,
        thinking_level,
        latest_compaction,
    )
