from __future__ import annotations

from typing import Any

from pi_agent.agent import AgentTool, AgentToolResult
from pi_agent.ai import CancellationToken

from ..mutation_queue import with_file_mutation_queue
from ..path_utils import normalize_tool_path, resolve_tool_path
from ..result import get_or_raise
from ..types import ExecutionEnv
from .common import require_string, text_result


def create_write_tool(env: ExecutionEnv) -> AgentTool:
    async def execute(
        _call_id: str,
        arguments: dict[str, Any],
        signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        _raise_if_cancelled(signal)
        raw_path = require_string(arguments, "path")
        normalized = normalize_tool_path(raw_path)
        content = require_string(arguments, "content")
        resolved = await resolve_tool_path(env, normalized, signal)
        data = content.encode("utf-8")

        async def mutate() -> AgentToolResult:
            existed = get_or_raise(await env.exists(resolved, signal))
            get_or_raise(await env.write_file(resolved, data, signal))
            action = "Updated" if existed else "Created"
            return text_result(
                f"{action} {raw_path} ({len(data)} bytes)",
                details={
                    "path": resolved,
                    "bytes": len(data),
                    "existed": existed,
                    "created": not existed,
                },
            )

        return await with_file_mutation_queue(env, resolved, mutate)

    return AgentTool(
        name="write",
        label="Write",
        description="Atomically create or replace a UTF-8 text file.",
        parameters={
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "content": {"type": "string"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
    )


def _raise_if_cancelled(signal: CancellationToken | None) -> None:
    if signal is not None:
        signal.raise_if_cancelled()
