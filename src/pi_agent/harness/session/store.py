from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

from pi_agent.ai import AssistantMessage, ToolCall, ToolResultMessage

from .codec import SessionCodecError, record_from_dict, record_to_dict
from .types import CursorEntry, MessageEntry, SessionHeader, SessionRecord, TreeEntry, is_tree_entry


class SessionError(RuntimeError):
    pass


class SessionCorruptionError(SessionError):
    def __init__(self, message: str, *, line: int | None = None, offset: int | None = None) -> None:
        super().__init__(message)
        self.line = line
        self.offset = offset


class SessionValidationError(SessionError):
    pass


@dataclass(slots=True, frozen=True)
class SessionWarning:
    code: str
    message: str
    line: int | None = None
    offset: int | None = None


@dataclass(slots=True)
class SessionLoadResult:
    records: list[SessionRecord]
    warnings: list[SessionWarning] = field(default_factory=list)
    valid_bytes: int = 0
    ignored_tail: bytes = b""

    @property
    def header(self) -> SessionHeader:
        if not self.records or not isinstance(self.records[0], SessionHeader):
            raise SessionValidationError("session header is missing")
        return self.records[0]


class SessionStore(Protocol):
    def load(self) -> SessionLoadResult: ...

    def append(self, record: SessionRecord) -> None: ...


class _FileLock:
    def __init__(self, file: BinaryIO) -> None:
        self.file = file

    def __enter__(self) -> None:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            self.file.seek(0)
            api = cast(Any, msvcrt)
            api.locking(self.file.fileno(), api.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX)

    def __exit__(self, *_args: object) -> None:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            self.file.seek(0)
            api = cast(Any, msvcrt)
            api.locking(self.file.fileno(), api.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)


class JsonlSessionStore:
    """Append-only JSONL session storage with recoverable final-tail corruption."""

    def __init__(self, path: str | Path, *, fsync: bool = False) -> None:
        self.path = Path(path)
        self.fsync = fsync
        self._thread_lock = threading.RLock()

    @classmethod
    def create(
        cls, path: str | Path, header: SessionHeader, *, fsync: bool = False
    ) -> JsonlSessionStore:
        store = cls(path, fsync=fsync)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        with store._thread_lock:
            try:
                with store.path.open("xb") as file:
                    file.write(_encode_record(header))
                    file.flush()
                    if fsync:
                        os.fsync(file.fileno())
            except FileExistsError as exc:
                raise SessionError(f"session already exists: {store.path}") from exc
        return store

    def load(self) -> SessionLoadResult:
        if not self.path.exists():
            raise SessionError(f"session does not exist: {self.path}")
        data = self.path.read_bytes()
        result = decode_jsonl(data)
        result.warnings.extend(validate_records(result.records))
        return result

    def append(self, record: SessionRecord) -> None:
        encoded = _encode_record(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.path.open("a+b") as file, _FileLock(file):
            file.seek(0)
            existing = file.read()
            if not existing:
                if not isinstance(record, SessionHeader):
                    raise SessionValidationError("the first record must be a session header")
            else:
                loaded = decode_jsonl(existing)
                if loaded.ignored_tail:
                    raise SessionCorruptionError(
                        (
                            "refusing to append after a corrupt final JSONL record; "
                            "repair the tail first"
                        ),
                        offset=loaded.valid_bytes,
                    )
                validate_records([*loaded.records, record])
                if isinstance(record, SessionHeader):
                    raise SessionValidationError("a session header may only be the first record")
                if not existing.endswith(b"\n"):
                    file.seek(0, os.SEEK_END)
                    file.write(b"\n")
            file.seek(0, os.SEEK_END)
            file.write(encoded)
            file.flush()
            if self.fsync:
                os.fsync(file.fileno())

    def repair_tail(self) -> SessionLoadResult:
        """Discard only an invalid, unterminated final record and preserve all valid bytes."""

        with self._thread_lock, self.path.open("r+b") as file, _FileLock(file):
            data = file.read()
            loaded = decode_jsonl(data)
            if loaded.ignored_tail:
                file.seek(loaded.valid_bytes)
                file.truncate()
                file.flush()
                if self.fsync:
                    os.fsync(file.fileno())
            return loaded


class InMemorySessionStore:
    def __init__(self, records: Iterable[SessionRecord] = ()) -> None:
        self.records = list(records)
        if self.records:
            validate_records(self.records)

    def load(self) -> SessionLoadResult:
        warnings = validate_records(self.records)
        return SessionLoadResult(list(self.records), warnings)

    def append(self, record: SessionRecord) -> None:
        validate_records([*self.records, record])
        self.records.append(record)


def _encode_record(record: SessionRecord) -> bytes:
    return (
        json.dumps(record_to_dict(record), ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def decode_jsonl(data: bytes) -> SessionLoadResult:
    records: list[SessionRecord] = []
    warnings: list[SessionWarning] = []
    offset = 0
    valid_bytes = 0
    ignored_tail = b""
    lines = data.splitlines(keepends=True)
    for index, raw_line in enumerate(lines, start=1):
        is_last = index == len(lines)
        body = raw_line.rstrip(b"\r\n")
        next_offset = offset + len(raw_line)
        if not body.strip():
            if is_last:
                valid_bytes = next_offset
                offset = next_offset
                continue
            raise SessionCorruptionError("blank JSONL record", line=index, offset=offset)
        try:
            text = body.decode("utf-8")
            value = json.loads(text)
            if not isinstance(value, dict):
                raise SessionCodecError("record must be a JSON object")
            record = record_from_dict(value)
        except (UnicodeDecodeError, json.JSONDecodeError, SessionCodecError, ValueError) as exc:
            if is_last and not raw_line.endswith((b"\n", b"\r")):
                ignored_tail = raw_line
                warnings.append(
                    SessionWarning(
                        "corrupt_tail",
                        f"ignored incomplete final JSONL record: {exc}",
                        line=index,
                        offset=offset,
                    )
                )
                break
            raise SessionCorruptionError(
                f"invalid session record: {exc}", line=index, offset=offset
            ) from exc
        records.append(record)
        valid_bytes = next_offset
        offset = next_offset
    if not data:
        valid_bytes = 0
    return SessionLoadResult(records, warnings, valid_bytes, ignored_tail)


def validate_records(records: list[SessionRecord]) -> list[SessionWarning]:
    if not records:
        raise SessionValidationError("session is empty")
    if not isinstance(records[0], SessionHeader):
        raise SessionValidationError("the first record must be a session header")
    if records[0].version != 1:
        raise SessionValidationError(f"unsupported session schema version: {records[0].version}")

    entries: dict[str, TreeEntry] = {}
    cursor: CursorEntry | None = None
    warnings: list[SessionWarning] = []
    for position, record in enumerate(records[1:], start=2):
        if isinstance(record, SessionHeader):
            raise SessionValidationError(f"duplicate session header at record {position}")
        if isinstance(record, CursorEntry):
            cursor = record
            continue
        if not is_tree_entry(record):  # pragma: no cover - union exhaustiveness
            raise SessionValidationError(f"unsupported record at position {position}")
        entry = record
        if entry.id in entries:
            raise SessionValidationError(f"duplicate entry id: {entry.id}")
        if entry.parent_id is not None and entry.parent_id not in entries:
            raise SessionValidationError(
                f"entry {entry.id} references unknown parent {entry.parent_id}"
            )
        entries[entry.id] = entry

    if cursor is not None and cursor.leaf_id is not None and cursor.leaf_id not in entries:
        raise SessionValidationError(f"cursor references unknown entry {cursor.leaf_id}")

    for entry in entries.values():
        if not isinstance(entry, MessageEntry) or not isinstance(entry.message, ToolResultMessage):
            continue
        tool_result = entry.message
        ancestor_id = entry.parent_id
        matched = False
        while ancestor_id is not None:
            ancestor = entries[ancestor_id]
            if isinstance(ancestor, MessageEntry) and isinstance(
                ancestor.message, AssistantMessage
            ):
                matched = any(
                    isinstance(block, ToolCall) and block.id == tool_result.tool_call_id
                    for block in ancestor.message.content
                )
                if matched:
                    break
            ancestor_id = ancestor.parent_id
        if not matched:
            raise SessionValidationError(
                f"tool result {entry.id} has no ancestor tool call {tool_result.tool_call_id}"
            )

    resolved_ids = {
        entry.message.tool_call_id
        for entry in entries.values()
        if isinstance(entry, MessageEntry) and isinstance(entry.message, ToolResultMessage)
    }
    for entry in entries.values():
        if not isinstance(entry, MessageEntry) or not isinstance(entry.message, AssistantMessage):
            continue
        for block in entry.message.content:
            if isinstance(block, ToolCall) and block.id not in resolved_ids:
                warnings.append(
                    SessionWarning(
                        "unresolved_tool_call",
                        (
                            f"tool call {block.id} has no recorded result; "
                            "the session may have been interrupted"
                        ),
                    )
                )
    return warnings
