from __future__ import annotations

import asyncio
import copy
import inspect
import json
from collections.abc import Awaitable
from typing import Any, TypeVar

from pi_agent.ai import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
    message_to_dict,
)
from pi_agent.harness.session import CompactionEntry, MessageEntry, SessionManager

from .token_estimator import calculate_context_tokens, estimate_context_tokens
from .types import (
    AfterCompactionHook,
    BeforeCompactionHook,
    CompactionPreparation,
    CompactionResult,
    CompactionSettings,
    FileOperations,
    Summarizer,
    SummaryResponse,
)

T = TypeVar("T")


class CompactionError(RuntimeError):
    pass


class CompactionCancelled(CompactionError):
    pass


def should_compact(
    messages: list[Message] | tuple[Message, ...], settings: CompactionSettings
) -> bool:
    return calculate_context_tokens(messages) >= settings.threshold


def find_turn_start_index(messages: list[Message] | tuple[Message, ...], index: int) -> int:
    if not 0 <= index <= len(messages):
        raise IndexError(index)
    if not messages:
        return 0
    for current in range(min(index, len(messages) - 1), -1, -1):
        if isinstance(messages[current], UserMessage):
            return current
    return 0


def safe_cut_indices(messages: list[Message] | tuple[Message, ...]) -> tuple[int, ...]:
    """Return boundaries that do not split an assistant tool batch from its results."""

    safe: list[int] = [0]
    pending: set[str] = set()
    for index, message in enumerate(messages):
        if isinstance(message, AssistantMessage):
            pending.update(block.id for block in message.content if isinstance(block, ToolCall))
        elif isinstance(message, ToolResultMessage):
            pending.discard(message.tool_call_id)
        boundary = index + 1
        if not pending:
            safe.append(boundary)
    return tuple(dict.fromkeys(safe))


def find_cut_point(
    messages: list[Message] | tuple[Message, ...],
    keep_recent_tokens: int,
) -> tuple[int, bool]:
    if not messages:
        return 0, False
    candidates = [index for index in safe_cut_indices(messages) if 0 < index < len(messages)]
    if not candidates:
        return 0, False

    # Match the upstream entry-index algorithm: walk backward until the desired
    # recent-token budget is reached, then choose the first safe boundary at or
    # after that position. A large final message may therefore be retained even
    # when it alone exceeds the target budget; dropping the entire live turn is
    # less useful than marking a split turn and summarizing its prefix.
    chosen = candidates[0]
    accumulated = 0
    for index in range(len(messages) - 1, -1, -1):
        accumulated += estimate_context_tokens(messages[index : index + 1])
        if accumulated < keep_recent_tokens:
            continue
        safe_after = next((candidate for candidate in candidates if candidate >= index), None)
        # A crossing inside the trailing ToolResult portion of a batch has no
        # valid first-kept entry after it. Summarize the complete batch rather
        # than retaining an over-budget batch or orphaning its tool result.
        chosen = len(messages) if safe_after is None else safe_after
        break

    # Prefer a complete user-turn boundary when it fits.
    turn_start = find_turn_start_index(messages, chosen)
    if (
        chosen < len(messages)
        and turn_start > 0
        and turn_start in candidates
        and estimate_context_tokens(messages[turn_start:]) <= keep_recent_tokens
    ):
        chosen = turn_start
    split_turn = chosen == len(messages) or not isinstance(messages[chosen], UserMessage)
    return chosen, split_turn


def collect_file_operations(messages: list[Message] | tuple[Message, ...]) -> FileOperations:
    read: set[str] = set()
    modified: set[str] = set()
    for message in messages:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if not isinstance(block, ToolCall):
                    continue
                path = _path_from_mapping(block.arguments)
                if path is None:
                    continue
                lowered = block.name.lower()
                if lowered in {"read", "grep", "find", "ls"}:
                    read.add(path)
                elif lowered in {"write", "edit", "patch", "apply_patch"}:
                    modified.add(path)
        elif isinstance(message, ToolResultMessage) and isinstance(message.details, dict):
            path = _path_from_mapping(message.details)
            operation = str(message.details.get("operation", message.tool_name)).lower()
            if path is not None:
                if operation in {"read", "grep", "find", "ls"}:
                    read.add(path)
                elif operation in {"write", "edit", "patch", "apply_patch"}:
                    modified.add(path)
            files = message.details.get("files")
            if isinstance(files, list):
                target = (
                    modified if operation in {"write", "edit", "patch", "apply_patch"} else read
                )
                target.update(str(item) for item in files if isinstance(item, str))
    return FileOperations(tuple(sorted(read)), tuple(sorted(modified)))


def _path_from_mapping(value: dict[str, Any]) -> str | None:
    for key in ("path", "file", "file_path", "filePath"):
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return None


def prepare_compaction(
    messages: list[Message] | tuple[Message, ...],
    settings: CompactionSettings,
    *,
    previous_summary: str | None = None,
    force: bool = False,
) -> CompactionPreparation:
    message_list = list(messages)
    estimated = calculate_context_tokens(message_list)
    if not force and estimated < settings.threshold:
        raise CompactionError(
            f"context does not require compaction: {estimated} < {settings.threshold} tokens"
        )
    cut_index, split_turn = find_cut_point(message_list, settings.keep_recent_tokens)
    if cut_index <= 0:
        raise CompactionError("no safe compaction cut point is available")
    summarized = tuple(message_list[:cut_index])
    kept = tuple(message_list[cut_index:])
    return CompactionPreparation(
        messages_to_summarize=summarized,
        messages_to_keep=kept,
        cut_index=cut_index,
        estimated_tokens=estimated,
        kept_tokens=estimate_context_tokens(kept),
        split_turn=split_turn,
        previous_summary=previous_summary,
        file_operations=collect_file_operations(summarized),
    )


def serialize_conversation(messages: list[Message] | tuple[Message, ...]) -> str:
    return "\n".join(
        json.dumps(message_to_dict(message), ensure_ascii=False, sort_keys=True, default=str)
        for message in messages
    )


async def compact(
    messages: list[Message] | tuple[Message, ...],
    settings: CompactionSettings,
    summarizer: Summarizer,
    *,
    previous_summary: str | None = None,
    force: bool = False,
    before: BeforeCompactionHook | None = None,
    after: AfterCompactionHook | None = None,
) -> CompactionResult:
    preparation = prepare_compaction(
        messages, settings, previous_summary=previous_summary, force=force
    )
    if before is not None:
        decision = await _maybe_await(before(preparation))
        if decision.cancel:
            raise CompactionCancelled("compaction was cancelled by a before hook")
        if decision.preparation is not None:
            preparation = decision.preparation

    conversation = serialize_conversation(preparation.messages_to_summarize)
    response: SummaryResponse | None = None
    last_error: Exception | None = None
    attempts = 0
    for attempts in range(1, settings.max_attempts + 1):
        try:
            raw = await _maybe_await(summarizer(conversation, preparation.previous_summary))
            response = raw if isinstance(raw, SummaryResponse) else SummaryResponse(str(raw))
            if not response.text.strip():
                raise CompactionError("summarizer returned an empty summary")
            break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if attempts >= settings.max_attempts:
                break
            await asyncio.sleep(0)
    if response is None:
        raise CompactionError(f"summary generation failed after {attempts} attempts: {last_error}")

    summary_message = UserMessage(settings.summary_prefix + response.text.strip())
    active = (summary_message, *copy.deepcopy(preparation.messages_to_keep))
    result = CompactionResult(
        summary=response.text.strip(),
        active_messages=active,
        cut_index=preparation.cut_index,
        tokens_before=preparation.estimated_tokens,
        tokens_after=estimate_context_tokens(active),
        split_turn=preparation.split_turn,
        usage=response.usage,
        file_operations=preparation.file_operations,
        attempts=attempts,
    )
    if after is not None:
        result = await _maybe_await(after(result))
    return result


async def compact_session(
    manager: SessionManager,
    settings: CompactionSettings,
    summarizer: Summarizer,
    *,
    force: bool = False,
    before: BeforeCompactionHook | None = None,
    after: AfterCompactionHook | None = None,
) -> tuple[CompactionEntry, CompactionResult]:
    active_entries = manager.active_entries()
    message_entries = [entry for entry in active_entries if isinstance(entry, MessageEntry)]
    previous_entry = next(
        (entry for entry in reversed(active_entries) if isinstance(entry, CompactionEntry)),
        None,
    )
    result = await compact(
        [entry.message for entry in message_entries],
        settings,
        summarizer,
        previous_summary=previous_entry.summary if previous_entry else None,
        force=force,
        before=before,
        after=after,
    )
    first_kept = (
        message_entries[result.cut_index].id if result.cut_index < len(message_entries) else None
    )
    entry = manager.append_compaction(
        result.summary,
        first_kept_entry_id=first_kept,
        tokens_before=result.tokens_before,
        tokens_after=result.tokens_after,
        details={
            "splitTurn": result.split_turn,
            "attempts": result.attempts,
            "usage": _usage_dict(result.usage),
            "fileOperations": {
                "read": list(result.file_operations.read),
                "modified": list(result.file_operations.modified),
            },
        },
    )
    return entry, result


def _usage_dict(usage: Usage) -> dict[str, Any]:
    return {
        "input": usage.input,
        "output": usage.output,
        "cacheRead": usage.cache_read,
        "cacheWrite": usage.cache_write,
        "totalTokens": usage.total_tokens,
        "cost": {
            "input": usage.cost.input,
            "output": usage.cost.output,
            "cacheRead": usage.cost.cache_read,
            "cacheWrite": usage.cost.cache_write,
            "total": usage.cost.total,
        },
    }


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value
