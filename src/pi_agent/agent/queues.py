from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .types import AgentMessage, QueueMode


@dataclass(slots=True)
class MessageQueue:
    """FIFO queue with pi's ``all``/``one-at-a-time`` drain semantics."""

    mode: QueueMode = "one-at-a-time"
    _items: deque[AgentMessage] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self._validate_mode(self.mode)

    def push(self, message: AgentMessage) -> None:
        self._items.append(message)

    def enqueue(self, message: AgentMessage) -> None:
        self.push(message)

    def drain(self) -> list[AgentMessage]:
        if not self._items:
            return []
        if self.mode == "one-at-a-time":
            return [self._items.popleft()]
        items = list(self._items)
        self._items.clear()
        return items

    def clear(self) -> None:
        self._items.clear()

    def set_mode(self, mode: QueueMode) -> None:
        self._validate_mode(mode)
        self.mode = mode

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    @staticmethod
    def _validate_mode(mode: QueueMode) -> None:
        if mode not in {"all", "one-at-a-time"}:
            raise ValueError(f"Unsupported queue mode: {mode!r}")


PendingMessageQueue = MessageQueue
