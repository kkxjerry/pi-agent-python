"""Context compaction and branch-summary helpers."""

from .branch import generate_branch_summary
from .compaction import (
    collect_file_operations,
    compact,
    deterministic_summary,
    prepare_compaction,
    serialize_conversation,
    should_compact,
)
from .controller import CompactionController
from .token_estimator import (
    calculate_context_tokens,
    estimate_context_tokens,
    estimate_message_tokens,
    estimate_tokens,
)
from .types import (
    AfterCompactHook,
    BeforeCompactHook,
    BranchSummaryResult,
    CompactResult,
    CompactionPreparation,
    CompactionSettings,
    FileOperations,
    Summarizer,
    SummaryResult,
)

__all__ = [
    "AfterCompactHook",
    "BeforeCompactHook",
    "BranchSummaryResult",
    "CompactResult",
    "CompactionController",
    "CompactionPreparation",
    "CompactionSettings",
    "FileOperations",
    "Summarizer",
    "SummaryResult",
    "calculate_context_tokens",
    "collect_file_operations",
    "compact",
    "deterministic_summary",
    "estimate_context_tokens",
    "estimate_message_tokens",
    "estimate_tokens",
    "generate_branch_summary",
    "prepare_compaction",
    "serialize_conversation",
    "should_compact",
]
