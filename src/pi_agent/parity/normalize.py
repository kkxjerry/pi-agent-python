from __future__ import annotations

import re
from typing import Any

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,}$", re.IGNORECASE)
_DYNAMIC_KEYS = {"timestamp", "createdAt", "updatedAt"}
_ID_KEYS = {"sessionId", "responseId", "messageId", "entryId"}


def normalize_value(value: Any, *, key: str | None = None) -> Any:
    if key in _DYNAMIC_KEYS and isinstance(value, (int, float, str)):
        return "<timestamp>"
    if key in _ID_KEYS and isinstance(value, str):
        return f"<{key}>"
    if isinstance(value, str) and _UUID.match(value) and key not in {"toolCallId"}:
        return "<uuid>"
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {
            child_key: normalize_value(child_value, key=child_key)
            for child_key, child_value in value.items()
        }
    return value
