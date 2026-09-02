from __future__ import annotations

from typing import Any

from pi_agent.agent import AgentTool, AgentToolResult
from pi_agent.ai import CancellationToken, ImageContent, TextContent

from ..image import detect_supported_image_mime_type, encode_base64
from ..path_utils import resolve_read_tool_path
from ..result import get_or_raise
from ..truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, truncate_head
from ..types import ExecutionEnv
from .common import optional_positive_int, require_string


def create_read_tool(
    env: ExecutionEnv,
    *,
    default_line_limit: int | None = None,
    max_chars: int = DEFAULT_MAX_BYTES,
    max_lines: int = DEFAULT_MAX_LINES,
) -> AgentTool:
    """Create the read tool.

    The official-style rendering is selected when ``default_line_limit`` is
    supplied: text is returned verbatim and a continuation hint is appended.
    The original Python Phase 7 numbered rendering remains the default for
    callers that already depend on it.
    """

    if default_line_limit is not None and default_line_limit <= 0:
        raise ValueError("default_line_limit must be positive")

    async def execute(
        _call_id: str,
        arguments: dict[str, Any],
        signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        _raise_if_cancelled(signal)
        raw_path = require_string(arguments, "path")
        offset = optional_positive_int(arguments, "offset") or 1
        requested_limit = optional_positive_int(arguments, "limit")
        resolved = await resolve_read_tool_path(env, raw_path, signal)
        info = get_or_raise(await env.file_info(resolved, signal))
        if info.kind != "file":
            raise ValueError(f"Not a regular file: {raw_path}")
        data = get_or_raise(await env.read_binary_file(resolved, signal))
        _raise_if_cancelled(signal)

        mime_type = detect_supported_image_mime_type(data)
        if mime_type is not None:
            if offset != 1 or requested_limit is not None:
                raise ValueError("offset and limit are not supported for images")
            return AgentToolResult(
                content=[
                    TextContent(f"Read image file [{mime_type}]"),
                    ImageContent(encode_base64(data), mime_type),
                ],
                details={
                    "path": resolved,
                    "size": len(data),
                    "mimeType": mime_type,
                    "image": True,
                },
            )
        if b"\x00" in data[:8_192]:
            raise ValueError(f"Binary files are not supported by read: {raw_path}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"File is not valid UTF-8: {raw_path}") from exc

        if default_line_limit is not None:
            return _render_verbatim(
                raw_path=raw_path,
                resolved=resolved,
                text=text,
                data_size=len(data),
                offset=offset,
                limit=requested_limit or default_line_limit,
                max_bytes=max_chars,
                max_lines=max_lines,
            )
        return _render_numbered(
            raw_path=raw_path,
            resolved=resolved,
            text=text,
            data_size=len(data),
            offset=offset,
            limit=requested_limit,
            max_bytes=max_chars,
            max_lines=max_lines,
        )

    return AgentTool(
        name="read",
        label="Read",
        description=(
            "Read a text file or a supported image with optional one-based offset and limit."
        ),
        parameters={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "offset": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
        execute=execute,
    )


def _render_verbatim(
    *,
    raw_path: str,
    resolved: str,
    text: str,
    data_size: int,
    offset: int,
    limit: int,
    max_bytes: int,
    max_lines: int,
) -> AgentToolResult:
    lines = text.splitlines(keepends=True)
    start = min(offset - 1, len(lines))
    stop = min(start + limit, len(lines))
    selected = "".join(lines[start:stop])
    truncated = truncate_head(selected, max_lines=max_lines, max_bytes=max_bytes)
    rendered = truncated.content
    has_more = stop < len(lines)
    if has_more:
        if rendered and not rendered.endswith("\n"):
            rendered += "\n"
        rendered += (
            f"\n[Showing lines {start + 1}-{stop} of {len(lines)}. "
            f"Use offset={stop + 1} to continue.]"
        )
    return AgentToolResult(
        content=[TextContent(rendered)],
        details={
            "path": resolved,
            "offset": offset,
            "limit": limit,
            "startLine": start + 1 if lines else 0,
            "endLine": stop,
            "totalLines": len(lines),
            "size": data_size,
            "truncated": truncated.truncated or has_more,
            "truncatedBy": truncated.truncated_by,
        },
    )


def _render_numbered(
    *,
    raw_path: str,
    resolved: str,
    text: str,
    data_size: int,
    offset: int,
    limit: int | None,
    max_bytes: int,
    max_lines: int,
) -> AgentToolResult:
    lines = text.splitlines()
    start = min(offset - 1, len(lines))
    stop = len(lines) if limit is None else min(start + limit, len(lines))
    selected = lines[start:stop]
    width = max(1, len(str(max(stop, 1))))
    numbered = "\n".join(
        f"{line_number:>{width}}: {line}"
        for line_number, line in enumerate(selected, start=start + 1)
    )
    truncated = truncate_head(numbered, max_lines=max_lines, max_bytes=max_bytes)
    header = f"{raw_path} (lines {start + 1}-{stop} of {len(lines)})"
    body = truncated.content or "[empty selection]"
    return AgentToolResult(
        content=[TextContent(f"{header}\n{body}")],
        details={
            "path": resolved,
            "offset": offset,
            "limit": limit,
            "startLine": start + 1 if lines else 0,
            "endLine": stop,
            "totalLines": len(lines),
            "size": data_size,
            "truncated": truncated.truncated,
            "truncatedBy": truncated.truncated_by,
        },
    )


def _raise_if_cancelled(signal: CancellationToken | None) -> None:
    if signal is not None:
        signal.raise_if_cancelled()
