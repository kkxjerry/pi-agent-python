from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias, TypeGuard

from pi_agent.ai import Message

SESSION_SCHEMA_VERSION = 1


def timestamp_ms() -> int:
    return time.time_ns() // 1_000_000


def new_entry_id(prefix: str = "entry") -> str:
    """Return a sortable-enough, collision-resistant local entry identifier."""

    return f"{prefix}_{timestamp_ms():013d}_{uuid.uuid4().hex[:16]}"


@dataclass(slots=True, frozen=True)
class SessionHeader:
    id: str
    cwd: str
    version: int = SESSION_SCHEMA_VERSION
    timestamp: int = field(default_factory=timestamp_ms)
    name: str | None = None
    parent_session_id: str | None = None
    type: Literal["session"] = field(default="session", init=False)


@dataclass(slots=True, frozen=True)
class MessageEntry:
    id: str
    parent_id: str | None
    message: Message
    timestamp: int = field(default_factory=timestamp_ms)
    type: Literal["message"] = field(default="message", init=False)


@dataclass(slots=True, frozen=True)
class ModelChangeEntry:
    id: str
    parent_id: str | None
    provider: str
    model_id: str
    timestamp: int = field(default_factory=timestamp_ms)
    type: Literal["model_change"] = field(default="model_change", init=False)


@dataclass(slots=True, frozen=True)
class ThinkingLevelChangeEntry:
    id: str
    parent_id: str | None
    thinking_level: str
    timestamp: int = field(default_factory=timestamp_ms)
    type: Literal["thinking_level_change"] = field(default="thinking_level_change", init=False)


@dataclass(slots=True, frozen=True)
class CompactionEntry:
    id: str
    parent_id: str | None
    summary: str
    first_kept_entry_id: str | None
    tokens_before: int
    tokens_after: int
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: int = field(default_factory=timestamp_ms)
    type: Literal["compaction"] = field(default="compaction", init=False)


@dataclass(slots=True, frozen=True)
class BranchSummaryEntry:
    id: str
    parent_id: str | None
    summary: str
    from_entry_id: str
    to_entry_id: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: int = field(default_factory=timestamp_ms)
    type: Literal["branch_summary"] = field(default="branch_summary", init=False)


@dataclass(slots=True, frozen=True)
class CustomEntry:
    id: str
    parent_id: str | None
    custom_type: str
    data: dict[str, Any]
    timestamp: int = field(default_factory=timestamp_ms)
    type: Literal["custom"] = field(default="custom", init=False)


@dataclass(slots=True, frozen=True)
class LabelEntry:
    id: str
    parent_id: str | None
    target_id: str
    label: str | None
    timestamp: int = field(default_factory=timestamp_ms)
    type: Literal["label"] = field(default="label", init=False)


@dataclass(slots=True, frozen=True)
class CursorEntry:
    """Append-only persisted branch selection; it is not part of the message tree."""

    leaf_id: str | None
    timestamp: int = field(default_factory=timestamp_ms)
    type: Literal["cursor"] = field(default="cursor", init=False)


TreeEntry: TypeAlias = (
    MessageEntry
    | ModelChangeEntry
    | ThinkingLevelChangeEntry
    | CompactionEntry
    | BranchSummaryEntry
    | CustomEntry
    | LabelEntry
)
SessionRecord: TypeAlias = SessionHeader | TreeEntry | CursorEntry

TREE_RECORD_TYPES = {
    "message",
    "model_change",
    "thinking_level_change",
    "compaction",
    "branch_summary",
    "custom",
    "label",
}


def is_tree_entry(record: SessionRecord) -> TypeGuard[TreeEntry]:
    return record.type in TREE_RECORD_TYPES
