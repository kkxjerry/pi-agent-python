from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pi_agent.ai import (
    AssistantMessage,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from .types import TextStyle, cell_width, pad_cells, truncate_cells, wrap_cells


class View(Protocol):
    def render(self, width: int, height: int) -> tuple[str, ...]: ...


@dataclass(slots=True)
class TranscriptView:
    messages: list[Message] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    scroll_offset: int = 0
    show_thinking: bool = True

    def set_messages(self, messages: list[Message]) -> None:
        self.messages = messages
        self.scroll_offset = 0

    def add_notice(self, value: str) -> None:
        if value:
            self.notices.append(value)
            self.scroll_offset = 0

    def clear_notices(self) -> None:
        self.notices.clear()

    def scroll(self, delta: int, *, viewport_height: int) -> None:
        lines = self._lines(80)
        maximum = max(0, len(lines) - max(1, viewport_height))
        self.scroll_offset = min(maximum, max(0, self.scroll_offset + delta))

    def render(self, width: int, height: int) -> tuple[str, ...]:
        if height <= 0:
            return ()
        lines = self._lines(width)
        end = max(0, len(lines) - self.scroll_offset)
        start = max(0, end - height)
        visible = lines[start:end]
        padding = [""] * max(0, height - len(visible))
        return tuple(pad_cells(line, width) for line in (*padding, *visible))

    def _lines(self, width: int) -> list[str]:
        output: list[str] = []
        for message in self.messages:
            output.extend(_message_lines(message, width, show_thinking=self.show_thinking))
            output.append("")
        for notice in self.notices:
            output.extend(f"  {line}" for line in wrap_cells(notice, max(1, width - 2)))
            output.append("")
        return output[:-1] if output else []


@dataclass(slots=True, frozen=True)
class StatusView:
    left: str
    right: str = ""

    def render(self, width: int) -> str:
        left = truncate_cells(self.left, width)
        remaining = max(0, width - cell_width(left))
        if not self.right or remaining < 2:
            return pad_cells(left, width)
        right = truncate_cells(self.right, remaining - 1)
        gap = max(1, width - cell_width(left) - cell_width(right))
        return pad_cells(left + " " * gap + right, width)


@dataclass(slots=True)
class CompletionView:
    items: list[Any] = field(default_factory=list)
    selected: int = 0

    def render(self, width: int, height: int) -> tuple[str, ...]:
        if not self.items or height <= 0:
            return ()
        self.selected %= len(self.items)
        start = min(max(0, self.selected - height + 1), max(0, len(self.items) - height))
        lines: list[str] = []
        for index, item in enumerate(self.items[start : start + height], start=start):
            marker = "> " if index == self.selected else "  "
            description = f" — {item.description}" if item.description else ""
            lines.append(pad_cells(truncate_cells(marker + item.label + description, width), width))
        return tuple(lines)


def style_text(value: str, style: TextStyle) -> str:
    prefix = style.ansi_prefix()
    return f"{prefix}{value}\x1b[0m" if prefix else value


def _message_lines(message: Message, width: int, *, show_thinking: bool) -> list[str]:
    content_width = max(1, width - 2)
    if isinstance(message, UserMessage):
        text = (
            message.content
            if isinstance(message.content, str)
            else "".join(block.text for block in message.content if isinstance(block, TextContent))
        )
        return ["You", *(f"  {line}" for line in wrap_cells(text, content_width))]
    if isinstance(message, AssistantMessage):
        lines = ["Assistant"]
        for block in message.content:
            if isinstance(block, TextContent):
                lines.extend(f"  {line}" for line in wrap_cells(block.text, content_width))
            elif isinstance(block, ThinkingContent) and show_thinking:
                lines.extend(
                    f"  · {line}" for line in wrap_cells(block.thinking, max(1, content_width - 2))
                )
            elif isinstance(block, ToolCall):
                arguments = ", ".join(f"{key}={value!r}" for key, value in block.arguments.items())
                lines.extend(
                    f"  → {line}"
                    for line in wrap_cells(f"{block.name}({arguments})", max(1, content_width - 2))
                )
        if message.stop_reason in {"error", "aborted"}:
            lines.append(f"  ! {message.error_message or message.stop_reason}")
        return lines
    if isinstance(message, ToolResultMessage):
        text = "".join(block.text for block in message.content if isinstance(block, TextContent))
        prefix = "Tool error" if message.is_error else "Tool"
        heading = f"{prefix}: {message.tool_name}"
        return [heading, *(f"  {line}" for line in wrap_cells(text, content_width))]
    return [str(message)]
