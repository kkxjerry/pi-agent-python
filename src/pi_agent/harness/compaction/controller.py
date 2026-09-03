from __future__ import annotations

from dataclasses import dataclass

from pi_agent.ai import CancellationToken, Message, Model

from .compaction import compact, should_compact
from .types import CompactResult, CompactionSettings, Summarizer


@dataclass(slots=True)
class CompactionController:
    settings: CompactionSettings
    summarizer: Summarizer
    previous_summary: str | None = None
    last_result: CompactResult | None = None

    def needed(self, messages: list[Message], model: Model) -> bool:
        return should_compact(
            messages,
            context_window=model.context_window,
            settings=self.settings,
        )

    async def prepare_next_provider_context(
        self,
        messages: list[Message],
        model: Model,
        signal: CancellationToken | None = None,
        *,
        force: bool = False,
    ) -> list[Message]:
        if not force and not self.needed(messages, model):
            return list(messages)
        result = await compact(
            list(messages),
            context_window=model.context_window,
            settings=self.settings,
            summarizer=self.summarizer,
            previous_summary=self.previous_summary,
            signal=signal,
        )
        self.previous_summary = result.summary
        self.last_result = result
        return list(result.messages)
