from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from pi_agent.ai import Message, Model, message_to_dict

from .context import active_branch, build_model_context
from .store import JsonlSessionStore, MemorySessionStore, SessionStore
from .types import SessionDocument, SessionEntry, SessionHeader, SessionInfo, SessionTreeNode


class SessionManager:
    """Mutable cursor over an append-only session tree."""

    def __init__(self, store: SessionStore, document: SessionDocument) -> None:
        self.store = store
        self.document = document
        self._current_id = document.current_id()
        self._lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        path: str | Path,
        *,
        cwd: str | Path,
        name: str | None = None,
        fsync: bool = False,
    ) -> SessionManager:
        store = JsonlSessionStore(path, fsync=fsync)
        document = await store.create(SessionHeader.create(cwd, name=name))
        return cls(store, document)

    @classmethod
    async def open(
        cls,
        path: str | Path,
        *,
        fsync: bool = False,
        repair_tail: bool = False,
    ) -> SessionManager:
        store = JsonlSessionStore(path, fsync=fsync)
        document = await store.load(allow_corrupt_tail=repair_tail)
        if document.corrupt_tail is not None:
            if not repair_tail:
                raise ValueError("session has a corrupt final JSONL record")
            document = await store.repair_tail()
        return cls(store, document)

    @classmethod
    async def memory(
        cls,
        *,
        cwd: str | Path,
        name: str | None = None,
    ) -> SessionManager:
        store = MemorySessionStore()
        document = await store.create(SessionHeader.create(cwd, name=name))
        return cls(store, document)

    @property
    def path(self) -> Path | None:
        return self.store.path

    @property
    def current_id(self) -> str | None:
        return self._current_id

    @property
    def info(self) -> SessionInfo:
        timestamps = [
            self.document.header.created_at,
            *(entry.timestamp for entry in self.document.entries),
        ]
        return SessionInfo(
            id=self.document.header.id,
            path=self.path,
            cwd=self.document.header.cwd,
            name=self.document.header.name,
            created_at=self.document.header.created_at,
            updated_at=max(timestamps),
            entry_count=len([entry for entry in self.document.entries if entry.type != "cursor"]),
            current_id=self._current_id,
        )

    async def append_entry(
        self,
        entry_type: str,
        data: dict[str, Any] | None = None,
        *,
        parent_id: str | None | object = ...,
    ) -> SessionEntry:
        async with self._lock:
            parent = self._current_id if parent_id is ... else parent_id
            entry = SessionEntry.create(
                entry_type,  # type: ignore[arg-type]
                parent_id=parent if isinstance(parent, str) or parent is None else None,
                data=data,
            )
            await self.store.append(entry)
            self.document.entries.append(entry)
            if entry.type != "cursor":
                self._current_id = entry.id
            return entry

    async def append_message(self, message: Message) -> SessionEntry:
        async with self._lock:
            entry = SessionEntry.for_message(message, parent_id=self._current_id)
            await self.store.append(entry)
            self.document.entries.append(entry)
            self._current_id = entry.id
            return entry

    async def record_model(self, model: Model) -> SessionEntry:
        return await self.append_entry(
            "model_change",
            {
                "provider": model.provider,
                "modelId": model.id,
                "api": model.api,
                "baseUrl": model.base_url,
            },
        )

    async def record_thinking_level(self, level: str) -> SessionEntry:
        return await self.append_entry("thinking_level_change", {"thinkingLevel": level})

    async def record_compaction(
        self,
        *,
        summary: str,
        first_kept_id: str | None,
        usage: dict[str, Any] | None = None,
        files: dict[str, list[str]] | None = None,
    ) -> SessionEntry:
        data: dict[str, Any] = {"summary": summary, "firstKeptId": first_kept_id}
        if usage is not None:
            data["usage"] = usage
        if files is not None:
            data["files"] = files
        return await self.append_entry("compaction", data)

    async def record_branch_summary(
        self,
        *,
        summary: str,
        from_id: str | None,
        to_id: str | None,
    ) -> SessionEntry:
        return await self.append_entry(
            "branch_summary",
            {"summary": summary, "fromId": from_id, "toId": to_id},
        )

    async def record_custom(self, name: str, data: Any) -> SessionEntry:
        return await self.append_entry("custom", {"name": name, "data": data})

    async def label(self, label: str, *, entry_id: str | None = None) -> SessionEntry:
        target = self._current_id if entry_id is None else entry_id
        if target is not None and target not in self.entry_map:
            raise KeyError(target)
        return await self.append_entry("label", {"label": label, "entryId": target})

    async def navigate(self, entry_id: str | None) -> None:
        if entry_id is not None and entry_id not in self.entry_map:
            raise KeyError(entry_id)
        async with self._lock:
            cursor = SessionEntry.create("cursor", parent_id=entry_id, data={})
            await self.store.append(cursor)
            self.document.entries.append(cursor)
            self._current_id = entry_id

    @property
    def entry_map(self) -> dict[str, SessionEntry]:
        return self.document.entry_map()

    def active_entries(self) -> list[SessionEntry]:
        return active_branch(self.document.entries, self._current_id)

    def model_context(self) -> list[Message]:
        return build_model_context(self.document.entries, self._current_id)

    def tree(self) -> tuple[SessionTreeNode, ...]:
        entries = [entry for entry in self.document.entries if entry.type != "cursor"]
        children: dict[str | None, list[SessionEntry]] = {}
        for entry in entries:
            children.setdefault(entry.parent_id, []).append(entry)

        def build(entry: SessionEntry) -> SessionTreeNode:
            return SessionTreeNode(
                entry=entry,
                children=tuple(build(child) for child in children.get(entry.id, [])),
            )

        return tuple(build(entry) for entry in children.get(None, []))

    def common_ancestor(self, left_id: str | None, right_id: str | None) -> str | None:
        left = [entry.id for entry in active_branch(self.document.entries, left_id)]
        right = [entry.id for entry in active_branch(self.document.entries, right_id)]
        common: str | None = None
        for left_entry, right_entry in zip(left, right, strict=False):
            if left_entry != right_entry:
                break
            common = left_entry
        return common

    async def fork(
        self,
        path: str | Path,
        *,
        name: str | None = None,
        fsync: bool = False,
    ) -> SessionManager:
        destination = Path(path).expanduser().resolve()
        store = JsonlSessionStore(destination, fsync=fsync)
        header = SessionHeader.create(
            self.document.header.cwd, name=name or self.document.header.name
        )
        branch = self.active_entries()
        await store.create(header)
        for entry in branch:
            await store.append(entry)
        return await SessionManager.open(destination, fsync=fsync)

    async def rename(self, name: str | None) -> None:
        async with self._lock:
            self.document.header = SessionHeader(
                id=self.document.header.id,
                cwd=self.document.header.cwd,
                created_at=self.document.header.created_at,
                version=self.document.header.version,
                name=name,
            )
            await self.store.rewrite(self.document)

    async def export(self, path: str | Path) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "header": self.document.header.to_dict(),
            "entries": [entry.to_dict() for entry in self.document.entries],
            "currentId": self._current_id,
        }
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, target)
        return target

    @classmethod
    async def import_file(
        cls,
        source: str | Path,
        destination: str | Path,
        *,
        fsync: bool = False,
    ) -> SessionManager:
        payload = json.loads(Path(source).expanduser().read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("header"), dict):
            raise ValueError("invalid exported session")
        document = SessionDocument(
            header=SessionHeader.from_dict(payload["header"]),
            entries=[SessionEntry.from_dict(item) for item in payload.get("entries", [])],
        )
        store = JsonlSessionStore(destination, fsync=fsync)
        await store.rewrite(document)
        manager = cls(store, document)
        current = payload.get("currentId")
        if current is None or isinstance(current, str):
            manager._current_id = current
        return manager

    async def delete(self) -> None:
        if self.path is not None:
            await asyncio.to_thread(self.path.unlink, missing_ok=True)

    def snapshot(self) -> dict[str, Any]:
        return {
            "session": self.document.header.to_dict(),
            "currentId": self._current_id,
            "entries": [entry.to_dict() for entry in self.document.entries],
            "messages": [message_to_dict(message) for message in self.model_context()],
        }
