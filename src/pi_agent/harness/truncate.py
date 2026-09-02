from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_LINES = 2_000
DEFAULT_MAX_BYTES = 50 * 1_024
GREP_MAX_LINE_LENGTH = 500


@dataclass(frozen=True, slots=True)
class TruncationResult:
    content: str
    truncated: bool
    truncated_by: str | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool
    first_line_exceeds_limit: bool
    max_lines: int
    max_bytes: int


@dataclass(frozen=True, slots=True)
class TruncatedText:
    """Compatibility view used by the early coding-tool API."""

    text: str
    truncated: bool
    original_chars: int
    original_lines: int


def format_size(size: int) -> str:
    if size < 1_024:
        return f"{size}B"
    if size < 1_024 * 1_024:
        return f"{size / 1_024:.1f}KB"
    return f"{size / (1_024 * 1_024):.1f}MB"


def truncate_head(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    _validate_limits(max_lines, max_bytes)
    total_bytes = _utf8_length(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return _unchanged(content, total_lines, total_bytes, max_lines, max_bytes)

    first_line = lines[0] if lines else ""
    if _utf8_length(first_line) > max_bytes:
        return TruncationResult(
            content="",
            truncated=True,
            truncated_by="bytes",
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=0,
            output_bytes=0,
            last_line_partial=False,
            first_line_exceeds_limit=True,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    output: list[str] = []
    output_bytes = 0
    truncated_by = "lines"
    for index, line in enumerate(lines[:max_lines]):
        line_bytes = _utf8_length(line) + (1 if index > 0 else 0)
        if output_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        output.append(line)
        output_bytes += line_bytes
    if len(output) >= max_lines and output_bytes <= max_bytes:
        truncated_by = "lines"
    rendered = "\n".join(output)
    return TruncationResult(
        content=rendered,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(output),
        output_bytes=_utf8_length(rendered),
        last_line_partial=False,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def truncate_tail(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    _validate_limits(max_lines, max_bytes)
    total_bytes = _utf8_length(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return _unchanged(content, total_lines, total_bytes, max_lines, max_bytes)

    output: list[str] = []
    output_bytes = 0
    truncated_by = "lines"
    last_line_partial = False
    for line in reversed(lines):
        if len(output) >= max_lines:
            break
        line_bytes = _utf8_length(line) + (1 if output else 0)
        if output_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not output:
                suffix = _truncate_utf8_suffix(line, max_bytes)
                output.insert(0, suffix)
                output_bytes = _utf8_length(suffix)
                last_line_partial = True
            break
        output.insert(0, line)
        output_bytes += line_bytes
    if len(output) >= max_lines and output_bytes <= max_bytes:
        truncated_by = "lines"
    rendered = "\n".join(output)
    return TruncationResult(
        content=rendered,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(output),
        output_bytes=_utf8_length(rendered),
        last_line_partial=last_line_partial,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> tuple[str, bool]:
    if max_chars < 0:
        raise ValueError("max_chars must not be negative")
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True


def truncate_text(
    text: str,
    *,
    max_chars: int,
    max_lines: int,
    preserve_tail: bool = False,
) -> TruncatedText:
    """Compatibility wrapper around the byte-aware truncation primitives.

    The earlier Python API named its limit ``max_chars``. ASCII coding output is
    equivalent to a byte limit; non-ASCII output is now conservatively bounded
    by UTF-8 bytes instead of being allowed to exceed the provider/tool budget.
    """

    result = (
        truncate_tail(text, max_lines=max_lines, max_bytes=max_chars)
        if preserve_tail
        else truncate_head(text, max_lines=max_lines, max_bytes=max_chars)
    )
    rendered = result.content
    if result.truncated:
        marker = (
            f"\n... output truncated from {result.total_bytes} bytes "
            f"or {result.total_lines} lines ...\n"
        )
        rendered = f"{marker}{rendered}" if preserve_tail else f"{rendered}{marker}"
    return TruncatedText(
        text=rendered,
        truncated=result.truncated,
        original_chars=len(text),
        original_lines=result.total_lines,
    )


def _unchanged(
    content: str,
    total_lines: int,
    total_bytes: int,
    max_lines: int,
    max_bytes: int,
) -> TruncationResult:
    return TruncationResult(
        content=content,
        truncated=False,
        truncated_by=None,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=total_lines,
        output_bytes=total_bytes,
        last_line_partial=False,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def _split_lines_for_counting(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def _truncate_utf8_suffix(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    selected: list[str] = []
    used = 0
    for character in reversed(value):
        size = _utf8_length(character)
        if used + size > max_bytes:
            break
        selected.append(character)
        used += size
    selected.reverse()
    return "".join(selected)


def _utf8_length(value: str) -> int:
    return len(value.encode("utf-8", errors="replace"))


def _validate_limits(max_lines: int, max_bytes: int) -> None:
    if max_lines < 0:
        raise ValueError("max_lines must not be negative")
    if max_bytes < 0:
        raise ValueError("max_bytes must not be negative")
