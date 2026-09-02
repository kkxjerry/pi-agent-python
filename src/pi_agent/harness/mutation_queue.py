from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar
from weakref import WeakKeyDictionary

from .result import get_or_raise
from .types import ExecutionEnv

T = TypeVar("T")


@dataclass(slots=True)
class _PathLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


@dataclass(slots=True)
class _EnvironmentState:
    registration: asyncio.Lock = field(default_factory=asyncio.Lock)
    paths: dict[str, _PathLock] = field(default_factory=dict)


_STATES: WeakKeyDictionary[object, _EnvironmentState] = WeakKeyDictionary()


async def with_file_mutation_queue(
    env: ExecutionEnv,
    path: str,
    operation: Callable[[], Awaitable[T]],
) -> T:
    """Serialize mutations targeting the same environment and canonical path."""

    state = _STATES.get(env)
    if state is None:
        state = _EnvironmentState()
        _STATES[env] = state

    absolute = get_or_raise(await env.absolute_path(path))
    canonical = await env.canonical_path(absolute)
    if canonical.ok:
        key = canonical.value
    elif canonical.error.code in {"not_found", "not_supported"}:
        key = absolute
    else:
        raise canonical.error

    async with state.registration:
        entry = state.paths.get(key)
        if entry is None:
            entry = _PathLock()
            state.paths[key] = entry
        entry.users += 1

    try:
        async with entry.lock:
            return await operation()
    finally:
        async with state.registration:
            entry.users -= 1
            if entry.users == 0 and state.paths.get(key) is entry:
                del state.paths[key]
