from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from pi_agent.ai import Message

from .types import CursorEntry, MessageEntry, SessionHeader, SessionRecord, TreeEntry, is_tree_entry


class SessionTreeError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class SessionPath:
    header: SessionHeader
    entries: tuple[TreeEntry, ...]

    @property
    def messages(self) -> list[Message]:
        return [entry.message for entry in self.entries if isinstance(entry, MessageEntry)]


class SessionTree:
    """Immutable tree projection over append-only session records."""

    def __init__(self, records: list[SessionRecord]) -> None:
        if not records or not isinstance(records[0], SessionHeader):
            raise SessionTreeError("session header is missing")
        self.header = records[0]
        self.entries: dict[str, TreeEntry] = {}
        self.order: list[str] = []
        self.children: dict[str | None, list[str]] = defaultdict(list)
        requested_leaf: str | None = None
        cursor_seen = False
        for record in records[1:]:
            if isinstance(record, CursorEntry):
                requested_leaf = record.leaf_id
                cursor_seen = True
                continue
            if not is_tree_entry(record):
                continue
            self.entries[record.id] = record
            self.order.append(record.id)
            self.children[record.parent_id].append(record.id)
        if requested_leaf is not None and requested_leaf not in self.entries:
            raise SessionTreeError(f"cursor references unknown entry {requested_leaf}")
        self.leaf_id = requested_leaf if cursor_seen else self._last_leaf()

    def _last_leaf(self) -> str | None:
        return self.order[-1] if self.order else None

    def get(self, entry_id: str) -> TreeEntry:
        try:
            return self.entries[entry_id]
        except KeyError as exc:
            raise SessionTreeError(f"unknown entry: {entry_id}") from exc

    def path_to(self, entry_id: str | None) -> SessionPath:
        if entry_id is None:
            return SessionPath(self.header, ())
        path: list[TreeEntry] = []
        seen: set[str] = set()
        current_id: str | None = entry_id
        while current_id is not None:
            if current_id in seen:
                raise SessionTreeError(f"cycle detected at entry {current_id}")
            seen.add(current_id)
            entry = self.get(current_id)
            path.append(entry)
            current_id = entry.parent_id
        path.reverse()
        return SessionPath(self.header, tuple(path))

    def active_path(self) -> SessionPath:
        return self.path_to(self.leaf_id)

    def ancestor_ids(self, entry_id: str | None, *, include_self: bool = True) -> list[str]:
        if entry_id is None:
            return []
        result: list[str] = []
        current: str | None = entry_id if include_self else self.get(entry_id).parent_id
        while current is not None:
            result.append(current)
            current = self.get(current).parent_id
        return result

    def common_ancestor(self, left_id: str | None, right_id: str | None) -> str | None:
        right = set(self.ancestor_ids(right_id))
        for candidate in self.ancestor_ids(left_id):
            if candidate in right:
                return candidate
        return None

    def child_ids(self, entry_id: str | None) -> tuple[str, ...]:
        return tuple(self.children.get(entry_id, ()))

    def leaf_ids(self) -> tuple[str, ...]:
        return tuple(entry_id for entry_id in self.order if not self.children.get(entry_id))

    def descendants(self, entry_id: str | None) -> tuple[str, ...]:
        result: list[str] = []
        stack = list(reversed(self.children.get(entry_id, ())))
        while stack:
            current = stack.pop()
            result.append(current)
            stack.extend(reversed(self.children.get(current, ())))
        return tuple(result)

    def branch_delta(self, from_id: str | None, to_id: str | None) -> tuple[TreeEntry, ...]:
        """Return entries on the destination branch after the common ancestor."""

        common = self.common_ancestor(from_id, to_id)
        path = self.path_to(to_id).entries
        if common is None:
            return path
        common_index = next(index for index, entry in enumerate(path) if entry.id == common)
        return path[common_index + 1 :]

    def labels(self) -> dict[str, str]:
        from .types import LabelEntry

        labels: dict[str, str] = {}
        for entry_id in self.order:
            entry = self.entries[entry_id]
            if isinstance(entry, LabelEntry):
                if entry.label is None:
                    labels.pop(entry.target_id, None)
                else:
                    labels[entry.target_id] = entry.label
        return labels
