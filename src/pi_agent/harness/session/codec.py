from __future__ import annotations

from typing import Any, cast

from pi_agent.ai import message_from_dict, message_to_dict

from .types import (
    BranchSummaryEntry,
    CompactionEntry,
    CursorEntry,
    CustomEntry,
    LabelEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionHeader,
    SessionRecord,
    ThinkingLevelChangeEntry,
)


class SessionCodecError(ValueError):
    pass


def record_to_dict(record: SessionRecord) -> dict[str, Any]:
    if isinstance(record, SessionHeader):
        value: dict[str, Any] = {
            "type": "session",
            "id": record.id,
            "version": record.version,
            "timestamp": record.timestamp,
            "cwd": record.cwd,
        }
        if record.name is not None:
            value["name"] = record.name
        if record.parent_session_id is not None:
            value["parentSessionId"] = record.parent_session_id
        return value
    if isinstance(record, CursorEntry):
        return {"type": "cursor", "leafId": record.leaf_id, "timestamp": record.timestamp}

    base: dict[str, Any] = {
        "type": record.type,
        "id": record.id,
        "parentId": record.parent_id,
        "timestamp": record.timestamp,
    }
    if isinstance(record, MessageEntry):
        base["message"] = message_to_dict(record.message)
    elif isinstance(record, ModelChangeEntry):
        base.update({"provider": record.provider, "modelId": record.model_id})
    elif isinstance(record, ThinkingLevelChangeEntry):
        base["thinkingLevel"] = record.thinking_level
    elif isinstance(record, CompactionEntry):
        base.update(
            {
                "summary": record.summary,
                "firstKeptEntryId": record.first_kept_entry_id,
                "tokensBefore": record.tokens_before,
                "tokensAfter": record.tokens_after,
                "details": record.details,
            }
        )
    elif isinstance(record, BranchSummaryEntry):
        base.update(
            {
                "summary": record.summary,
                "fromEntryId": record.from_entry_id,
                "toEntryId": record.to_entry_id,
                "details": record.details,
            }
        )
    elif isinstance(record, CustomEntry):
        base.update({"customType": record.custom_type, "data": record.data})
    elif isinstance(record, LabelEntry):
        base.update({"targetId": record.target_id, "label": record.label})
    else:  # pragma: no cover - guarded by the type alias
        raise SessionCodecError(f"Unsupported session record: {type(record).__name__}")
    return base


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise SessionCodecError(f"{key} must be a non-empty string")
    return item


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise SessionCodecError(f"{key} must be a string or null")
    return item


def _timestamp(value: dict[str, Any]) -> int:
    item = value.get("timestamp", 0)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise SessionCodecError("timestamp must be a non-negative integer")
    return int(item)


def _details(value: dict[str, Any], key: str = "details") -> dict[str, Any]:
    item = value.get(key, {})
    if not isinstance(item, dict):
        raise SessionCodecError(f"{key} must be an object")
    return cast(dict[str, Any], item)


def record_from_dict(value: dict[str, Any]) -> SessionRecord:
    record_type = value.get("type")
    if record_type == "session":
        version = value.get("version", 1)
        if isinstance(version, bool) or not isinstance(version, int):
            raise SessionCodecError("version must be an integer")
        return SessionHeader(
            id=_required_string(value, "id"),
            cwd=_required_string(value, "cwd"),
            version=version,
            timestamp=_timestamp(value),
            name=_optional_string(value, "name"),
            parent_session_id=_optional_string(value, "parentSessionId"),
        )
    if record_type == "cursor":
        return CursorEntry(leaf_id=_optional_string(value, "leafId"), timestamp=_timestamp(value))

    entry_id = _required_string(value, "id")
    parent_id = _optional_string(value, "parentId")
    timestamp = _timestamp(value)
    if record_type == "message":
        message = value.get("message")
        if not isinstance(message, dict):
            raise SessionCodecError("message must be an object")
        return MessageEntry(entry_id, parent_id, message_from_dict(message), timestamp)
    if record_type == "model_change":
        return ModelChangeEntry(
            entry_id,
            parent_id,
            _required_string(value, "provider"),
            _required_string(value, "modelId"),
            timestamp,
        )
    if record_type == "thinking_level_change":
        return ThinkingLevelChangeEntry(
            entry_id,
            parent_id,
            _required_string(value, "thinkingLevel"),
            timestamp,
        )
    if record_type == "compaction":
        before = value.get("tokensBefore", 0)
        after = value.get("tokensAfter", 0)
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in (before, after)
        ):
            raise SessionCodecError("compaction token counts must be non-negative integers")
        return CompactionEntry(
            entry_id,
            parent_id,
            str(value.get("summary", "")),
            _optional_string(value, "firstKeptEntryId"),
            before,
            after,
            _details(value),
            timestamp,
        )
    if record_type == "branch_summary":
        return BranchSummaryEntry(
            entry_id,
            parent_id,
            str(value.get("summary", "")),
            _required_string(value, "fromEntryId"),
            _required_string(value, "toEntryId"),
            _details(value),
            timestamp,
        )
    if record_type == "custom":
        return CustomEntry(
            entry_id,
            parent_id,
            _required_string(value, "customType"),
            _details(value, "data"),
            timestamp,
        )
    if record_type == "label":
        return LabelEntry(
            entry_id,
            parent_id,
            _required_string(value, "targetId"),
            _optional_string(value, "label"),
            timestamp,
        )
    raise SessionCodecError(f"Unknown session record type: {record_type!r}")
