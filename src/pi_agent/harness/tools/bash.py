from __future__ import annotations

import asyncio
from typing import Any

from pi_agent.agent import AgentTool, AgentToolResult
from pi_agent.ai import CancellationToken, TextContent

from ..result import get_or_raise
from ..truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, format_size, truncate_tail
from ..types import ExecutionEnv, ShellExecOptions, StreamName
from .common import optional_positive_int, require_string, text_result


def create_bash_tool(
    env: ExecutionEnv,
    *,
    default_timeout: float = 120.0,
    max_chars: int = DEFAULT_MAX_BYTES,
    max_lines: int = DEFAULT_MAX_LINES,
) -> AgentTool:
    async def execute(
        _call_id: str,
        arguments: dict[str, Any],
        signal: CancellationToken | None,
        update: Any,
    ) -> AgentToolResult:
        command = require_string(arguments, "command")
        requested_timeout = optional_positive_int(arguments, "timeout")
        timeout = float(requested_timeout) if requested_timeout is not None else default_timeout

        async def on_output(stream_name: StreamName, chunk: str) -> None:
            update(
                AgentToolResult(
                    content=[TextContent(chunk)],
                    details={"stream": stream_name, "partial": True},
                )
            )

        execution = await env.exec(
            command,
            ShellExecOptions(
                cwd=env.cwd,
                timeout=timeout,
                abort_signal=signal,
                on_output=on_output,
            ),
        )
        if not execution.ok:
            if execution.error.code == "timeout":
                raise TimeoutError(str(execution.error))
            if execution.error.code == "aborted":
                raise asyncio.CancelledError(str(execution.error))
            raise execution.error
        result = get_or_raise(execution)
        rendered_output = result.output
        truncated = truncate_tail(
            rendered_output,
            max_lines=max_lines,
            max_bytes=max_chars,
        )
        rendered = truncated.content or "[command produced no output]"
        if truncated.truncated:
            rendered = (
                f"[Output truncated; showing the final {truncated.output_lines} lines / "
                f"{format_size(truncated.output_bytes)} of {format_size(truncated.total_bytes)}]\n"
                f"{rendered}"
            )
        rendered = f"{rendered}\n\nProcess exited with code {result.exit_code}"
        return text_result(
            rendered,
            details={
                "command": command,
                "exitCode": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "durationSeconds": result.duration_seconds,
                "truncated": truncated.truncated,
                "truncatedBy": truncated.truncated_by,
            },
        )

    return AgentTool(
        name="bash",
        label="Bash",
        description="Execute a shell command in the current working directory.",
        parameters={
            "type": "object",
            "required": ["command"],
            "properties": {
                "command": {"type": "string", "minLength": 1},
                "timeout": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
    )
