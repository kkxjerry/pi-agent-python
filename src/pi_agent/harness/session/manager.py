from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from pi_agent.ai import Message

from .store import (
    InMemorySessionStore,
    JsonlSessionStore,
    SessionError,
    SessionLoadResult,
    SessionStore,
)
from .tree import SessionTree
from .types import (
    BranchSummaryEntry,
    CompactionEntry,
    CursorEntry,
    CustomEntry,
    LabelEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionHeader,
    ThinkingLevelChangeEntry,
    TreeEntry,
    new_entry_id,
)


@dataclass(slots=True, frozen=True)
class SessionInfo:
    id: str
    path: Path
    cwd: str
    name: str | None
    modified_ns: int
    leaf_count: int
    entry_count: int
    warnings: tuple[str, ...] = ()


class SessionManager:
    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self._loaded = store.load()
        self.tree = SessionTree(self._loaded.records)

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        cwd: str,
        name: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        fsync: bool = False,
    ) -> SessionManager:
        header = SessionHeader(
            id=session_id or new_entry_id("session"),
            cwd=cwd,
            name=name,
            parent_session_id=parent_session_id,
        )
        return cls(JsonlSessionStore.create(path, header, fsync=fsync))

    @classmethod
    def in_memory(
        cls,
        *,
        cwd: str,
        name: str | None = None,
        session_id: str | None = None,
    ) -> SessionManager:
        header = SessionHeader(id=session_id or new_entry_id("session"), cwd=cwd, name=name)
        return cls(InMemorySessionStore([header]))

    @property
    def header(self) -> SessionHeader:
        return self.tree.header

    @property
    def leaf_id(self) -> str | None:
        return self.tree.leaf_id

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(warning.message for warning in self._loaded.warnings)

    def reload(self) -> SessionLoadResult:
        self._loaded = self.store.load()
        self.tree = SessionTree(self._loaded.records)
        return self._loaded

    def _append(self, entry: TreeEntry) -> TreeEntry:
        self.store.append(entry)
        self.store.append(CursorEntry(entry.id))
        self.reload()
        return entry

    def append_message(self, message: Message, *, entry_id: str | None = None) -> MessageEntry:
        return self._append(
            MessageEntry(entry_id or new_entry_id("message"), self.leaf_id, message)
        )  # type: ignore[return-value]

    def append_model_change(
        self, provider: str, model_id: str, *, entry_id: str | None = None
    ) -> ModelChangeEntry:
        return self._append(
            ModelChangeEntry(entry_id or new_entry_id("model"), self.leaf_id, provider, model_id)
        )  # type: ignore[return-value]

    def append_thinking_level(
        self, thinking_level: str, *, entry_id: str | None = None
    ) -> ThinkingLevelChangeEntry:
        return self._append(
            ThinkingLevelChangeEntry(
                entry_id or new_entry_id("thinking"), self.leaf_id, thinking_level
            )
        )  # type: ignore[return-value]

    def append_compaction(
        self,
        summary: str,
        *,
        first_kept_entry_id: str | None,
        tokens_before: int,
        tokens_after: int,
        details: dict[str, object] | None = None,
        entry_id: str | None = None,
    ) -> CompactionEntry:
        return self._append(
            CompactionEntry(
                entry_id or new_entry_id("compaction"),
                self.leaf_id,
                summary,
                first_kept_entry_id,
                tokens_before,
                tokens_after,
                dict(details or {}),
            )
        )  # type: ignore[return-value]

    def append_branch_summary(
        self,
        summary: str,
        *,
        from_entry_id: str,
        to_entry_id: str,
        details: dict[str, object] | None = None,
        entry_id: str | None = None,
    ) -> BranchSummaryEntry:
        return self._append(
            BranchSummaryEntry(
                entry_id or new_entry_id("branch"),
                self.leaf_id,
                summary,
                from_entry_id,
                to_entry_id,
                dict(details or {}),
            )
        )  # type: ignore[return-value]

    def append_custom(
        self,
        custom_type: str,
        data: dict[str, object],
        *,
        entry_id: str | None = None,
    ) -> CustomEntry:
        return self._append(
            CustomEntry(entry_id or new_entry_id("custom"), self.leaf_id, custom_type, dict(data))
        )  # type: ignore[return-value]

    def set_label(
        self, target_id: str, label: str | None, *, entry_id: str | None = None
    ) -> LabelEntry:
        if target_id != self.header.id and target_id not in self.tree.entries:
            raise SessionError(f"cannot label unknown target {target_id}")
        return self._append(
            LabelEntry(entry_id or new_entry_id("label"), self.leaf_id, target_id, label)
        )  # type: ignore[return-value]

    def navigate(self, entry_id: str | None, *, persist: bool = True) -> None:
        if entry_id is not None:
            self.tree.get(entry_id)
        if persist:
            self.store.append(CursorEntry(entry_id))
            self.reload()
        else:
            self.tree.leaf_id = entry_id

    def fork(self, entry_id: str | None) -> None:
        """Move the cursor; the next appended entry creates a new branch."""

        self.navigate(entry_id)

    def active_messages(self) -> list[Message]:
        return self.tree.active_path().messages

    def active_entries(self) -> tuple[TreeEntry, ...]:
        return self.tree.active_path().entries


class SessionRepository:
    def __init__(self, directory: str | Path, *, suffix: str = ".jsonl") -> None:
        self.directory = Path(directory)
        self.suffix = suffix

    def _path_for(self, session_id: str) -> Path:
        if not session_id or any(char in session_id for char in ("/", "\\", "\0")):
            raise SessionError("invalid session id")
        return self.directory / f"{session_id}{self.suffix}"

    def create(
        self,
        *,
        cwd: str,
        name: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
    ) -> SessionManager:
        actual_id = session_id or new_entry_id("session")
        self.directory.mkdir(parents=True, exist_ok=True)
        return SessionManager.create(
            self._path_for(actual_id),
            cwd=cwd,
            name=name,
            session_id=actual_id,
            parent_session_id=parent_session_id,
        )

    def open(self, session_id: str) -> SessionManager:
        return SessionManager(JsonlSessionStore(self._path_for(session_id)))

    def list(self) -> list[SessionInfo]:
        if not self.directory.exists():
            return []
        result: list[SessionInfo] = []
        for path in sorted(self.directory.glob(f"*{self.suffix}")):
            try:
                manager = SessionManager(JsonlSessionStore(path))
                stat = path.stat()
                result.append(
                    SessionInfo(
                        id=manager.header.id,
                        path=path,
                        cwd=manager.header.cwd,
                        name=manager.tree.labels().get(manager.header.id, manager.header.name),
                        modified_ns=stat.st_mtime_ns,
                        leaf_count=len(manager.tree.leaf_ids()),
                        entry_count=len(manager.tree.entries),
                        warnings=manager.warnings,
                    )
                )
            except SessionError as exc:
                stat = path.stat()
                result.append(
                    SessionInfo(
                        id=path.stem,
                        path=path,
                        cwd="",
                        name=None,
                        modified_ns=stat.st_mtime_ns,
                        leaf_count=0,
                        entry_count=0,
                        warnings=(str(exc),),
                    )
                )
        result.sort(key=lambda item: item.modified_ns, reverse=True)
        return result

    def delete(self, session_id: str) -> None:
        path = self._path_for(session_id)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise SessionError(f"session does not exist: {session_id}") from exc

    def rename(self, session_id: str, name: str | None) -> None:
        manager = self.open(session_id)
        manager.set_label(manager.header.id, name)

    def export(self, session_id: str, destination: str | Path) -> Path:
        source = self._path_for(session_id)
        if not source.is_file():
            raise SessionError(f"session does not exist: {session_id}")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target

    def import_file(self, source: str | Path, *, session_id: str | None = None) -> SessionManager:
        loaded_store = JsonlSessionStore(source)
        loaded = loaded_store.load()
        imported_id = session_id or loaded.header.id
        target = self._path_for(imported_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise SessionError(f"session already exists: {imported_id}")
        if imported_id == loaded.header.id:
            shutil.copyfile(source, target)
            return self.open(imported_id)

        header = SessionHeader(
            id=imported_id,
            cwd=loaded.header.cwd,
            name=loaded.header.name,
            parent_session_id=loaded.header.id,
        )
        target_store = JsonlSessionStore.create(target, header)
        for record in loaded.records[1:]:
            target_store.append(record)
        return self.open(imported_id)
