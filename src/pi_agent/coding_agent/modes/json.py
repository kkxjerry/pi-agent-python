from __future__ import annotations

import inspect
import json
import sys
from collections.abc import Awaitable, Callable
from typing import TextIO

from pi_agent.agent import AgentEvent

from ..agent_session import AgentSession
from ..events import agent_event_to_dict

JsonWriter = TextIO | Callable[[str], None | Awaitable[None]]


async def run_json_mode(
    session: AgentSession,
    prompt: str,
    *,
    output: JsonWriter | None = None,
) -> int:
    """Emit one complete JSON object per LF-delimited Agent event."""

    writer = output or sys.stdout

    async def listener(event: AgentEvent) -> None:
        await write_json_line(writer, agent_event_to_dict(event))

    unsubscribe = session.subscribe(listener)
    try:
        result = await session.prompt(prompt)
    finally:
        unsubscribe()
    return 0 if result.succeeded else 1


async def write_json_line(writer: JsonWriter, value: object) -> None:
    line = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    if callable(writer):
        result = writer(line)
        if inspect.isawaitable(result):
            await result
        return
    writer.write(line)
    flush = getattr(writer, "flush", None)
    if flush is not None:
        result = flush()
        if inspect.isawaitable(result):
            await result
