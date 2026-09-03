from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pi_agent.ai import Message
from pi_agent.harness.session import CompactionEntry, SessionManager, reconstruct_context

from .compaction import compact_session, should_compact
from .types import (
    AfterCompactionHook,
    BeforeCompactionHook,
    CompactionResult,
    CompactionSettings,
    Summarizer,
)


@dataclass(slots=True, frozen=True)
class CompactionDecision:
    compacted: bool
    messages: tuple[Message, ...]
    entry: CompactionEntry | None = None
    result: CompactionResult | None = None


class CompactionController:
    """Model-boundary compaction gate used after tool results and before a provider call."""

    def __init__(
        self,
        settings: CompactionSettings,
        summarizer: Summarizer,
        *,
        before: BeforeCompactionHook | None = None,
        after: AfterCompactionHook | None = None,
    ) -> None:
        self.settings = settings
        self.summarizer = summarizer
        self.before = before
        self.after = after
        self._lock = asyncio.Lock()

    async def prepare_next_provider_context(
        self,
        manager: SessionManager,
    ) -> CompactionDecision:
        current = reconstruct_context(
            manager.tree.active_path(), compaction_prefix=self.settings.summary_prefix
        ).messages
        if not should_compact(current, self.settings):
            return CompactionDecision(False, current)
        async with self._lock:
            # Reconstruct inside the lock so simultaneous turn completions cannot
            # append duplicate compaction records for the same active leaf.
            latest = reconstruct_context(
                manager.tree.active_path(), compaction_prefix=self.settings.summary_prefix
            ).messages
            if not should_compact(latest, self.settings):
                return CompactionDecision(False, latest)
            entry, result = await compact_session(
                manager,
                self.settings,
                self.summarizer,
                before=self.before,
                after=self.after,
            )
            reconstructed = reconstruct_context(
                manager.tree.active_path(), compaction_prefix=self.settings.summary_prefix
            )
            return CompactionDecision(True, reconstructed.messages, entry, result)
