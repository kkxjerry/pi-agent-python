from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeAlias

from pi_agent.ai import Message, Usage


@dataclass(slots=True, frozen=True)
class CompactionSettings:
    context_window: int
    reserve_tokens: int = 16_384
    keep_recent_tokens: int = 20_000
    summary_prefix: str = (
        "The conversation before this point was compacted. Continue from this summary:\n\n"
    )
    max_attempts: int = 2

    def __post_init__(self) -> None:
        if self.context_window <= 0:
            raise ValueError("context_window must be positive")
        if self.reserve_tokens < 0:
            raise ValueError("reserve_tokens must be non-negative")
        if self.keep_recent_tokens <= 0:
            raise ValueError("keep_recent_tokens must be positive")
        if self.reserve_tokens >= self.context_window:
            raise ValueError("reserve_tokens must be smaller than context_window")
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")

    @property
    def threshold(self) -> int:
        return self.context_window - self.reserve_tokens


@dataclass(slots=True, frozen=True)
class FileOperations:
    read: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class CompactionPreparation:
    messages_to_summarize: tuple[Message, ...]
    messages_to_keep: tuple[Message, ...]
    cut_index: int
    estimated_tokens: int
    kept_tokens: int
    split_turn: bool
    previous_summary: str | None = None
    file_operations: FileOperations = field(default_factory=FileOperations)


@dataclass(slots=True, frozen=True)
class SummaryResponse:
    text: str
    usage: Usage = field(default_factory=Usage.zero)


@dataclass(slots=True, frozen=True)
class CompactionResult:
    summary: str
    active_messages: tuple[Message, ...]
    cut_index: int
    tokens_before: int
    tokens_after: int
    split_turn: bool
    usage: Usage
    file_operations: FileOperations
    attempts: int


# Compatibility name retained from the discarded Phase 17 draft. Both names
# represent the same provider-neutral summary payload.
SummaryResult = SummaryResponse


class Summarizer(Protocol):
    def __call__(
        self,
        conversation: str,
        previous_summary: str | None = None,
    ) -> SummaryResponse | str | Awaitable[SummaryResponse | str]: ...


@dataclass(slots=True, frozen=True)
class BeforeCompactionDecision:
    cancel: bool = False
    preparation: CompactionPreparation | None = None


BeforeCompactionHook: TypeAlias = Callable[
    [CompactionPreparation], BeforeCompactionDecision | Awaitable[BeforeCompactionDecision]
]
AfterCompactionHook: TypeAlias = Callable[
    [CompactionResult], CompactionResult | Awaitable[CompactionResult]
]
