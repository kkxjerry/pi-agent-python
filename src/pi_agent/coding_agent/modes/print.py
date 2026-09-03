from __future__ import annotations

import inspect
import sys
from collections.abc import Awaitable, Callable
from typing import Any, TextIO

from pi_agent.agent import AgentEvent, MessageEndEvent, MessageUpdateEvent
from pi_agent.ai import AssistantMessage, TextDeltaEvent, message_text

from ..agent_session import AgentSession

Writer = TextIO | Callable[[str], None | Awaitable[None]]


async def run_print_mode(
    session: AgentSession,
    prompt: str,
    *,
    stdout: Writer | None = None,
    stderr: Writer | None = None,
) -> int:
    """Run one prompt and stream only assistant text to stdout."""

    out = stdout or sys.stdout
    err = stderr or sys.stderr
    emitted_text = False
    ended_with_newline = False

    async def listener(event: AgentEvent) -> None:
        nonlocal emitted_text, ended_with_newline
        if isinstance(event, MessageUpdateEvent) and isinstance(
            event.assistant_message_event, TextDeltaEvent
        ):
            delta = event.assistant_message_event.delta
            if delta:
                await _write(out, delta)
                emitted_text = True
                ended_with_newline = delta.endswith("\n")
        elif isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
            if not emitted_text:
                text = message_text(event.message)
                if text:
                    await _write(out, text)
                    emitted_text = True
                    ended_with_newline = text.endswith("\n")

    unsubscribe = session.subscribe(listener)
    try:
        result = await session.prompt(prompt)
    finally:
        unsubscribe()
    if emitted_text and not ended_with_newline:
        await _write(out, "\n")
    if not result.succeeded:
        message = result.error_message or "Agent run failed"
        await _write(err, f"{message}\n")
        return 1
    return 0


async def _write(writer: Writer, value: str) -> None:
    if callable(writer):
        result = writer(value)
        if inspect.isawaitable(result):
            await result
        return
    writer.write(value)
    flush = getattr(writer, "flush", None)
    if flush is not None:
        result = flush()
        if inspect.isawaitable(result):
            await result
