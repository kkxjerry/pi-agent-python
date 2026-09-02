from __future__ import annotations

from typing import Any

from pi_agent.agent import AgentToolResult
from pi_agent.ai import TextContent


def require_string(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def optional_positive_int(arguments: dict[str, Any], name: str) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def text_result(text: str, *, details: Any = None) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text)],
        details={} if details is None else details,
    )
