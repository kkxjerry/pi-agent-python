from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import TypeAlias

from pi_agent.coding_agent.approval import ApprovalDecision, ApprovalRequest

ApprovalChangeListener: TypeAlias = Callable[[], Awaitable[None] | None]


class TuiApprovalBroker:
    """Serialize approval prompts so the interactive view owns user input."""

    def __init__(self) -> None:
        self._pending: ApprovalRequest | None = None
        self._future: asyncio.Future[ApprovalDecision] | None = None
        self._lock = asyncio.Lock()
        self._listener: ApprovalChangeListener | None = None

    @property
    def pending(self) -> ApprovalRequest | None:
        return self._pending

    def set_change_listener(self, listener: ApprovalChangeListener | None) -> None:
        self._listener = listener

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        async with self._lock:
            if self._future is not None:
                return ApprovalDecision(
                    "deny",
                    "another approval request is already pending",
                )
            loop = asyncio.get_running_loop()
            future: asyncio.Future[ApprovalDecision] = loop.create_future()
            self._pending = request
            self._future = future
            await self._notify()
            try:
                return await future
            except asyncio.CancelledError:
                if not future.done():
                    future.set_result(ApprovalDecision("deny", "approval cancelled"))
                raise
            finally:
                self._pending = None
                self._future = None
                await self._notify()

    def decide(self, decision: ApprovalDecision) -> bool:
        future = self._future
        if future is None or future.done():
            return False
        future.set_result(decision)
        return True

    def cancel_all(self) -> None:
        future = self._future
        if future is not None and not future.done():
            future.set_result(ApprovalDecision("deny", "interactive approval closed"))

    async def _notify(self) -> None:
        if self._listener is None:
            return
        value = self._listener()
        if inspect.isawaitable(value):
            await value


__all__ = ["ApprovalChangeListener", "TuiApprovalBroker"]
