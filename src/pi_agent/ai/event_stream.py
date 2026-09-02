from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any, Generic, TypeVar, cast

from .events import AssistantMessageEvent, event_result, is_terminal_event
from .types import AssistantMessage

T = TypeVar("T")
R = TypeVar("R")
_MISSING = object()
_DONE = object()


class EventStream(Generic[T, R]):
    """Async iterable with an independently awaitable final result.

    Its observable behavior follows the pinned TypeScript EventStream: terminal
    events are still yielded, pushes after completion are ignored, and `result()`
    resolves from the terminal event or an explicit `end(result)` call.
    """

    def __init__(self, is_complete: Callable[[T], bool], extract_result: Callable[[T], R]) -> None:
        self._queue: deque[T] = deque()
        self._waiting: deque[asyncio.Future[T | object]] = deque()
        self._done = False
        self._is_complete = is_complete
        self._extract_result = extract_result
        self._result: R | object = _MISSING
        self._result_ready = asyncio.Event()
        self._producer_tasks: set[asyncio.Task[Any]] = set()

    @property
    def done(self) -> bool:
        return self._done

    def attach_task(self, task: asyncio.Task[Any]) -> None:
        """Retain a producer task until it finishes."""

        self._producer_tasks.add(task)
        task.add_done_callback(self._producer_tasks.discard)

    def push(self, event: T) -> None:
        if self._done:
            return
        if self._is_complete(event):
            self._done = True
            self._result = self._extract_result(event)
            self._result_ready.set()
        while self._waiting:
            waiter = self._waiting.popleft()
            if waiter.done():
                continue
            waiter.set_result(event)
            break
        else:
            self._queue.append(event)

    def end(self, result: R | object = _MISSING) -> None:
        self._done = True
        if result is not _MISSING and not self._result_ready.is_set():
            self._result = cast(R, result)
            self._result_ready.set()
        while self._waiting:
            waiter = self._waiting.popleft()
            if not waiter.done():
                waiter.set_result(_DONE)

    async def __aiter__(self) -> AsyncIterator[T]:
        while True:
            if self._queue:
                yield self._queue.popleft()
                continue
            if self._done:
                return
            waiter: asyncio.Future[T | object] = asyncio.get_running_loop().create_future()
            self._waiting.append(waiter)
            item = await waiter
            if item is _DONE:
                return
            yield cast(T, item)

    async def result(self) -> R:
        await self._result_ready.wait()
        return cast(R, self._result)


class AssistantMessageEventStream(EventStream[AssistantMessageEvent, AssistantMessage]):
    def __init__(self) -> None:
        super().__init__(is_terminal_event, event_result)


def create_assistant_message_event_stream() -> AssistantMessageEventStream:
    return AssistantMessageEventStream()
