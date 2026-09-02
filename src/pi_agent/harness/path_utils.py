from __future__ import annotations

import re
import unicodedata

from pi_agent.ai import CancellationToken

from .result import get_or_raise
from .types import ExecutionEnv

_UNICODE_SPACES = re.compile("[\u00a0\u2000-\u200a\u202f\u205f\u3000]")
_NARROW_NO_BREAK_SPACE = "\u202f"


def normalize_tool_path(path: str) -> str:
    normalized = _UNICODE_SPACES.sub(" ", path)
    return normalized[1:] if normalized.startswith("@") else normalized


async def resolve_tool_path(
    env: ExecutionEnv,
    path: str,
    signal: CancellationToken | None = None,
) -> str:
    return get_or_raise(await env.absolute_path(normalize_tool_path(path), signal))


async def resolve_read_tool_path(
    env: ExecutionEnv,
    path: str,
    signal: CancellationToken | None = None,
) -> str:
    resolved = await resolve_tool_path(env, path, signal)
    variants = (
        resolved,
        re.sub(r" (AM|PM)\.", rf"{_NARROW_NO_BREAK_SPACE}\1.", resolved, flags=re.IGNORECASE),
        unicodedata.normalize("NFD", resolved),
        resolved.replace("'", "\u2019"),
        unicodedata.normalize("NFD", resolved).replace("'", "\u2019"),
    )
    seen: set[str] = set()
    for variant in variants:
        if variant in seen:
            continue
        seen.add(variant)
        if get_or_raise(await env.exists(variant, signal)):
            return variant
    return resolved
