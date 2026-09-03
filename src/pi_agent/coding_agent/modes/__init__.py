"""Headless product modes sharing one :class:`AgentSession`."""

from __future__ import annotations

import sys
from typing import TextIO

from ..agent_session import AgentSession
from .json_event import parse_json_line, serialize_json_line, to_json_event
from .print_mode import PrintModeOptions, run_print_mode
from .rpc import RpcServer, run_rpc_mode, run_rpc_stdio


async def run_json_mode(
    session: AgentSession,
    prompt: str,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    return await run_print_mode(
        session,
        PrintModeOptions(
            output_mode="json",
            initial_message=prompt,
            stdout=stdout,
            stderr=stderr,
        ),
    )


__all__ = [
    "PrintModeOptions",
    "RpcServer",
    "parse_json_line",
    "run_json_mode",
    "run_print_mode",
    "run_rpc_mode",
    "run_rpc_stdio",
    "serialize_json_line",
    "to_json_event",
]
