from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pi_agent.ai import Message, message_from_dict, message_to_dict

SessionEntryType: TypeAlias = Literal[
    "message",
    "model_change",
    "thinking_level_change",
    "compaction",
    "branch_summary",
    "custom",
    "label",
    "cursor",
]


def timestamp_ms() -> int:
    return time.time_ns() // 1_000_000


def new_id(prefix: str = "entry") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True, slots=True)
class SessionHeader:
    id: str
    cwd: str
    created_at: int = field(default_factory=timestamp_ms)
    version: int = 1
    name: str | None = None

    @classmethod
    def create(cls, cwd: str | Path, *, name: str | None = None) -> SessionHeader:
        return cls(id=new_id("session"), cwd=str(Path(cwd).expanduser().resolve()), name=name)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "type": "session",
            "version": self.version,
            "id": self.id,
            "cwd": self.cwd,
            "createdAt": self.created_at,
        }
        if self.name is not None:
            value["name"] = self.name
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SessionHeader:
        if value.get("type") != "session":
            raise ValueError("first JSONL record must be a session header")
        session_id = value.get("id")
        cwd = value.get("cwd")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session header id must be a non-empty string")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("session header cwd must be a non-empty string")
        return cls(
            id=session_id,
            cwd=cwd,
            created_at=int(value.get("createdAt", 0)),
            version=int(value.get("version", 1)),
            name=value.get("name") if isinstance(value.get("name"), str) else None,
        )


@dataclass(frozen=True, slots=True)
class SessionEntry:
    type: SessionEntryType
    id: str
    parent_id: str | None
    timestamp: int
    data: dict[str, Any]

    @classmethod
    def create(
        cls,
        entry_type: SessionEntryType,
        *,
        parent_id: str | None,
        data: dict[str, Any] | None = None,
        entry_id: str | None = None,
        timestamp: int | None = None,
    ) -> SessionEntry:
        return cls(
            type=entry_type,
            id=entry_id or new_id(),
            parent_id=parent_id,
            timestamp=timestamp if timestamp is not None else timestamp_ms(),
            data=dict(data or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "parentId": self.parent_id,
            "timestamp": self.timestamp,
            **self.data,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SessionEntry:
        entry_type = value.get("type")
        allowed = {
            "message",
            "model_change",
            "thinking_level_change",
            "compaction",
            "branch_summary",
            "custom",
            "label",
            "cursor",
        }
        if entry_type not in allowed:
            raise ValueError(f"unknown session entry type: {entry_type!r}")
        entry_id = value.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("session entry id must be a non-empty string")
        parent_id = value.get("parentId")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError("parentId must be a string or null")
        data = {
            key: item
            for key, item in value.items()
            if key not in {"type", "id", "parentId", "timestamp"}
        }
        return cls(
            type=entry_type,
            id=entry_id,
            parent_id=parent_id,
            timestamp=int(value.get("timestamp", 0)),
            data=data,
        )

    @property
    def message(self) -> Message | None:
        raw = self.data.get("message")
        if self.type != "message" or not isinstance(raw, dict):
            return None
        return message_from_dict(raw)

    @classmethod
    def for_message(cls, message: Message, *, parent_id: str | None) -> SessionEntry:
        return cls.create(
            "message",
            parent_id=parent_id,
            data={"message": message_to_dict(message)},
        )


@dataclass(slots=True)
class SessionDocument:
    header: SessionHeader
    entries: list[SessionEntry] = field(default_factory=list)
    corrupt_tail: bytes | None = None

    def entry_map(self) -> dict[str, SessionEntry]:
        return {entry.id: entry for entry in self.entries if entry.type != "cursor"}

    def current_id(self) -> str | None:
        explicit_cursor = False
        cursor: str | None = None
        for entry in self.entries:
            if entry.type == "cursor":
                explicit_cursor = True
                cursor = entry.parent_id
            else:
                cursor = entry.id
        return cursor if explicit_cursor or self.entries else None


@dataclass(frozen=True, slots=True)
class SessionTreeNode:
    entry: SessionEntry
    children: tuple[SessionTreeNode, ...] = ()


@dataclass(frozen=True, slots=True)
class SessionInfo:
    id: str
    path: Path | None
    cwd: str
    name: str | None
    created_at: int
    updated_at: int
    entry_count: int
    current_id: str | None
