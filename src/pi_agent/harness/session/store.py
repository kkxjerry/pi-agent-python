from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from .types import SessionDocument, SessionEntry, SessionHeader


class SessionCorruptionError(ValueError):
    def __init__(self, message: str, *, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


class SessionStore(Protocol):
    path: Path | None

    async def create(self, header: SessionHeader) -> SessionDocument: ...

    async def load(self, *, allow_corrupt_tail: bool = False) -> SessionDocument: ...

    async def append(self, entry: SessionEntry) -> None: ...

    async def rewrite(self, document: SessionDocument) -> None: ...


class JsonlSessionStore:
    """Append-only JSONL session storage with explicit tail recovery."""

    def __init__(self, path: str | Path, *, fsync: bool = False) -> None:
        self.path = Path(path).expanduser().resolve()
        self.fsync = fsync
        self._lock = asyncio.Lock()

    async def create(self, header: SessionHeader) -> SessionDocument:
        async with self._lock:
            await asyncio.to_thread(self._create_sync, header)
        return SessionDocument(header=header)

    async def load(self, *, allow_corrupt_tail: bool = False) -> SessionDocument:
        async with self._lock:
            return await asyncio.to_thread(self._load_sync, allow_corrupt_tail)

    async def append(self, entry: SessionEntry) -> None:
        async with self._lock:
            await asyncio.to_thread(self._append_sync, entry)

    async def rewrite(self, document: SessionDocument) -> None:
        async with self._lock:
            await asyncio.to_thread(self._rewrite_sync, document)

    async def repair_tail(self) -> SessionDocument:
        document = await self.load(allow_corrupt_tail=True)
        if document.corrupt_tail is None:
            return document
        document.corrupt_tail = None
        await self.rewrite(document)
        return document

    def _create_sync(self, header: SessionHeader) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size:
            raise FileExistsError(str(self.path))
        self._atomic_write([header.to_dict()])

    def _load_sync(self, allow_corrupt_tail: bool) -> SessionDocument:
        if not self.path.exists():
            raise FileNotFoundError(str(self.path))
        raw = self.path.read_bytes()
        if not raw:
            raise SessionCorruptionError("session file is empty")
        lines = raw.splitlines(keepends=True)
        records: list[dict[str, object]] = []
        corrupt_tail: bytes | None = None
        for index, encoded in enumerate(lines, start=1):
            is_last = index == len(lines)
            has_terminator = encoded.endswith((b"\n", b"\r"))
            payload = encoded.rstrip(b"\r\n")
            if not payload.strip():
                continue
            try:
                decoded = payload.decode("utf-8")
                value = json.loads(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if is_last and not has_terminator and allow_corrupt_tail:
                    corrupt_tail = encoded
                    break
                raise SessionCorruptionError(
                    f"invalid session JSONL at line {index}: {exc}", line=index
                ) from exc
            if not isinstance(value, dict):
                raise SessionCorruptionError(
                    f"session JSONL line {index} must be an object", line=index
                )
            records.append(value)
        if not records:
            raise SessionCorruptionError("session file has no complete records")
        header = SessionHeader.from_dict(records[0])
        entries = [SessionEntry.from_dict(record) for record in records[1:]]
        document = SessionDocument(header=header, entries=entries, corrupt_tail=corrupt_tail)
        validate_document(document)
        return document

    def _append_sync(self, entry: SessionEntry) -> None:
        if not self.path.exists():
            raise FileNotFoundError(str(self.path))
        encoded = (
            json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        with self.path.open("r+b") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.seek(0, os.SEEK_END)
            handle.write(encoded)
            handle.flush()
            if self.fsync:
                os.fsync(handle.fileno())

    def _rewrite_sync(self, document: SessionDocument) -> None:
        self._atomic_write(
            [document.header.to_dict(), *(entry.to_dict() for entry in document.entries)]
        )

    def _atomic_write(self, records: Iterable[dict[str, object]]) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                for record in records:
                    handle.write(
                        (
                            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                        ).encode("utf-8")
                    )
                handle.flush()
                if self.fsync:
                    os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    """Cross-process lock used for JSONL reads, appends, and atomic rewrites."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            runtime = cast(Any, msvcrt)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            runtime.locking(handle.fileno(), runtime.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                runtime.locking(handle.fileno(), runtime.LK_UNLCK, 1)
        else:
            import fcntl

            runtime = cast(Any, fcntl)
            runtime.flock(handle.fileno(), runtime.LOCK_EX)
            try:
                yield
            finally:
                runtime.flock(handle.fileno(), runtime.LOCK_UN)


class MemorySessionStore:
    def __init__(self) -> None:
        self.path: Path | None = None
        self._document: SessionDocument | None = None
        self._lock = asyncio.Lock()

    async def create(self, header: SessionHeader) -> SessionDocument:
        async with self._lock:
            if self._document is not None:
                raise FileExistsError("memory session already exists")
            self._document = SessionDocument(header=header)
            return SessionDocument(header=header)

    async def load(self, *, allow_corrupt_tail: bool = False) -> SessionDocument:
        del allow_corrupt_tail
        async with self._lock:
            if self._document is None:
                raise FileNotFoundError("memory session does not exist")
            return SessionDocument(
                header=self._document.header,
                entries=list(self._document.entries),
                corrupt_tail=self._document.corrupt_tail,
            )

    async def append(self, entry: SessionEntry) -> None:
        async with self._lock:
            if self._document is None:
                raise FileNotFoundError("memory session does not exist")
            candidate = SessionDocument(
                header=self._document.header,
                entries=[*self._document.entries, entry],
            )
            validate_document(candidate)
            self._document.entries.append(entry)

    async def rewrite(self, document: SessionDocument) -> None:
        validate_document(document)
        async with self._lock:
            self._document = SessionDocument(document.header, list(document.entries))


def validate_document(document: SessionDocument) -> None:
    known: dict[str, SessionEntry] = {}
    for index, entry in enumerate(document.entries, start=2):
        if entry.id in known:
            raise SessionCorruptionError(f"duplicate session entry id {entry.id!r}", line=index)
        if entry.type == "cursor":
            if entry.parent_id is not None and entry.parent_id not in known:
                raise SessionCorruptionError(
                    f"cursor references unknown entry {entry.parent_id!r}", line=index
                )
            continue
        if entry.parent_id is not None and entry.parent_id not in known:
            raise SessionCorruptionError(
                f"entry {entry.id!r} references unknown parent {entry.parent_id!r}", line=index
            )
        if entry.type == "message":
            message = entry.message
            if message is None:
                raise SessionCorruptionError(
                    f"message entry {entry.id!r} has no message", line=index
                )
            if message.role == "toolResult":
                ancestor = entry.parent_id
                matched = False
                while ancestor is not None:
                    parent = known[ancestor]
                    parent_message = parent.message
                    if parent_message is not None and parent_message.role == "assistant":
                        if any(
                            getattr(block, "id", None) == message.tool_call_id
                            for block in parent_message.content
                        ):
                            matched = True
                            break
                    ancestor = parent.parent_id
                if not matched:
                    raise SessionCorruptionError(
                        f"orphan ToolResult {message.tool_call_id!r}", line=index
                    )
        known[entry.id] = entry
