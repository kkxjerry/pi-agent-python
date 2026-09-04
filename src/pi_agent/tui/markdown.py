from __future__ import annotations

import re

from .width import wrap_ansi

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
UNDERLINE = "\x1b[4m"
RESET = "\x1b[0m"

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_LINK = re.compile(r"\[([^]]+)]\(([^)]+)\)")


def render_markdown(text: str, width: int) -> list[str]:
    if width <= 0:
        raise ValueError("markdown width must be positive")
    lines: list[str] = []
    in_code = False
    language = ""
    for raw in text.replace("\r\n", "\n").split("\n"):
        stripped = raw.strip()
        if stripped.startswith("```"):
            if in_code:
                lines.append(f"{DIM}+{'-' * max(0, width - 1)}{RESET}")
                in_code = False
                language = ""
            else:
                language = stripped[3:].strip()
                label = f" {language} " if language else ""
                lines.append(f"{DIM}+{label}{'-' * max(0, width - len(label) - 1)}{RESET}")
                in_code = True
            continue
        if in_code:
            for code_line in wrap_ansi(raw.expandtabs(4), max(1, width - 2)):
                lines.append(f"{DIM}|{RESET} {code_line}")
            continue
        if not raw:
            lines.append("")
            continue
        heading = len(raw) - len(raw.lstrip("#"))
        if heading and heading <= 6 and raw[heading : heading + 1] == " ":
            content = _inline(raw[heading + 1 :])
            prefix = BOLD if heading <= 2 else UNDERLINE
            lines.extend(wrap_ansi(f"{prefix}{content}{RESET}", width))
            continue
        if raw.startswith("> "):
            content = _inline(raw[2:])
            for quote_line in wrap_ansi(content, max(1, width - 2)):
                lines.append(f"{DIM}|{RESET} {quote_line}")
            continue
        bullet = re.match(r"^(\s*)([-*+] |\d+[.)] )(.*)$", raw)
        if bullet is not None:
            indentation, marker, bullet_content = bullet.groups()
            prefix = f"{indentation}{marker}"
            wrapped_lines = wrap_ansi(_inline(bullet_content), max(1, width - len(prefix)))
            lines.append(prefix + wrapped_lines[0])
            lines.extend(" " * len(prefix) + line for line in wrapped_lines[1:])
            continue
        lines.extend(wrap_ansi(_inline(raw), width))
    if in_code:
        lines.append(f"{DIM}+{'-' * max(0, width - 1)}{RESET}")
    return lines or [""]


def _inline(text: str) -> str:
    value = _LINK.sub(
        lambda match: f"{UNDERLINE}{match.group(1)}{RESET} {DIM}<{match.group(2)}>{RESET}",
        text,
    )
    value = _INLINE_CODE.sub(lambda match: f"{DIM}`{match.group(1)}`{RESET}", value)
    value = _BOLD.sub(lambda match: f"{BOLD}{match.group(1)}{RESET}", value)
    return _ITALIC.sub(lambda match: f"{ITALIC}{match.group(1)}{RESET}", value)


__all__ = ["BOLD", "DIM", "ITALIC", "RESET", "UNDERLINE", "render_markdown"]
