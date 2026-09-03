from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeAlias

from pi_agent.ai import CancellationToken, Message, Usage


@dataclass(frozen=True, slots=True)
class FileOperations:
    read: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()

    def merge(self, other: FileOperations) -> FileOperations:
        return FileOperations(
            read=tuple(dict.fromkeys((*self.read, *other.read))),
            modified=tuple(dict.fromkeys((*self.modified, *other.modified))),
        )

    def to_dict(self) -> dict[str, list[str]]:
        return {"read": list(self.read), "modified": list(self.modified)}


@dataclass(slots=True)
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: int = 16_384
    keep_recent_tokens: int = 20_000
    retry_count: int = 1
    summary_prefix: str = "Summary of the earlier conversation:"


@dataclass(frozen=True, slots=True)
class SummaryResult:
    summary: str
    usage: Usage = field(default_factory=Usage.zero)


class Summarizer(Protocol):
    def __call__(
        self,
        messages: list[Message],
        previous_summary: str | None,
        signal: CancellationToken | None,
    ) -> Awaitable[SummaryResult | str] | SummaryResult | str: ...


@dataclass(frozen=True, slots=True)
class CompactionPreparation:
    context_tokens: int
    threshold_tokens: int
    cut_index: int
    summarized: tuple[Message, ...]
    retained: tuple[Message, ...]
    previous_summary: str | None
    files: FileOperations


@dataclass(frozen=True, slots=True)
class CompactResult:
    summary: str
    messages: tuple[Message, ...]
    summarized_count: int
    retained_count: int
    context_tokens_before: int
    estimated_tokens_after: int
    usage: Usage
    files: FileOperations
    cut_index: int


@dataclass(frozen=True, slots=True)
class BranchSummaryResult:
    summary: str
    usage: Usage
    files: FileOperations
    from_id: str | None
    to_id: str | None


BeforeCompactHook: TypeAlias = Callable[
    [CompactionPreparation],
    Awaitable[CompactionPreparation | None] | CompactionPreparation | None,
]
AfterCompactHook: TypeAlias = Callable[[CompactResult], Awaitable[None] | None]
