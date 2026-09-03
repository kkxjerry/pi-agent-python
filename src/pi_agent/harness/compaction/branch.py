from __future__ import annotations

import asyncio
import inspect
from typing import TypeVar

from pi_agent.ai import CancellationToken, Message, Usage

from ..types import BranchSummaryError
from .compaction import collect_file_operations
from .types import BranchSummaryResult, Summarizer, SummaryResult

T = TypeVar("T")


async def generate_branch_summary(
    messages: list[Message],
    *,
    summarizer: Summarizer,
    previous_summary: str | None = None,
    signal: CancellationToken | None = None,
    from_id: str | None = None,
    to_id: str | None = None,
) -> BranchSummaryResult:
    if signal is not None and signal.cancelled:
        raise BranchSummaryError("aborted", signal.reason)
    try:
        raw = summarizer(messages, previous_summary, signal)
        if inspect.isawaitable(raw):
            raw = await raw
        result = raw if isinstance(raw, SummaryResult) else SummaryResult(str(raw), Usage.zero())
        if not result.summary.strip():
            raise ValueError("summarizer returned an empty branch summary")
        return BranchSummaryResult(
            summary=result.summary.strip(),
            usage=result.usage,
            files=collect_file_operations(messages),
            from_id=from_id,
            to_id=to_id,
        )
    except asyncio.CancelledError as exc:
        raise BranchSummaryError("aborted", str(exc) or "Branch summary aborted", exc) from exc
    except BranchSummaryError:
        raise
    except Exception as exc:
        raise BranchSummaryError("summarization_failed", str(exc), exc) from exc
