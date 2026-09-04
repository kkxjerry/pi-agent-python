from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from pi_agent.ai import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from .completion import Completion
from .markdown import BOLD, DIM, RESET, render_markdown
from .width import pad_cells, truncate_text, wrap_ansi


class Component(Protocol):
    def render(self, width: int) -> list[str]: ...


@dataclass(slots=True, frozen=True)
class TextBlock:
    text: str
    markdown: bool = False

    def render(self, width: int) -> list[str]:
        return render_markdown(self.text, width) if self.markdown else wrap_ansi(self.text, width)


@dataclass(slots=True, frozen=True)
class Spacer:
    height: int = 1

    def render(self, width: int) -> list[str]:
        del width
        return [""] * max(0, self.height)


@dataclass(slots=True)
class VBox:
    children: list[Component] = field(default_factory=list)
    spacing: int = 0

    def render(self, width: int) -> list[str]:
        result: list[str] = []
        for index, child in enumerate(self.children):
            if index:
                result.extend("" for _ in range(max(0, self.spacing)))
            result.extend(child.render(width))
        return result


@dataclass(slots=True, frozen=True)
class MessageComponent:
    message: Message
    max_tool_lines: int = 12
    expanded: bool = False

    def render(self, width: int) -> list[str]:
        if isinstance(self.message, UserMessage):
            return _render_user(self.message, width)
        if isinstance(self.message, AssistantMessage):
            return _render_assistant(self.message, width)
        return _render_tool_result(
            self.message,
            width,
            max_lines=self.max_tool_lines,
            expanded=self.expanded,
        )


@dataclass(slots=True, frozen=True)
class ToolProgressComponent:
    tool_name: str
    status: str
    detail: str = ""

    def render(self, width: int) -> list[str]:
        marker = {"running": "*", "success": "+", "error": "!"}.get(self.status, "-")
        detail = (
            f" {truncate_text(self.detail, max(1, width // 2), ellipsis='...')}"
            if self.detail
            else ""
        )
        return wrap_ansi(f"{DIM}{marker} {self.tool_name}{RESET}{detail}", width)


@dataclass(slots=True, frozen=True)
class CompletionOverlay:
    candidates: tuple[Completion, ...]
    selected: int = 0
    max_rows: int = 8

    def render(self, width: int) -> list[str]:
        result: list[str] = []
        for index, candidate in enumerate(self.candidates[: self.max_rows]):
            marker = ">" if index == self.selected else " "
            description = f" {DIM}{candidate.description}{RESET}" if candidate.description else ""
            result.append(
                truncate_text(
                    f"{marker} {candidate.display}{description}",
                    width,
                    ellipsis="...",
                )
            )
        return result


@dataclass(slots=True, frozen=True)
class StatusBar:
    left: str
    right: str = ""

    def render(self, width: int) -> list[str]:
        right = truncate_text(self.right, width)
        available = max(0, width - len(right) - (1 if right else 0))
        left = truncate_text(self.left, available, ellipsis="...")
        gap = max(1 if right else 0, width - len(left) - len(right))
        return [pad_cells(left + " " * gap + right, width)]


def _render_user(message: UserMessage, width: int) -> list[str]:
    content: list[str] = []
    if isinstance(message.content, str):
        content.append(message.content)
    else:
        for block in message.content:
            if isinstance(block, TextContent):
                content.append(block.text)
            elif isinstance(block, ImageContent):
                content.append(f"[image: {block.mime_type}]")
    rendered = render_markdown("\n".join(content), max(1, width - 2))
    return [f"{BOLD}You{RESET} {rendered[0]}", *("  " + line for line in rendered[1:])]


def _render_assistant(message: AssistantMessage, width: int) -> list[str]:
    lines: list[str] = []
    for block in message.content:
        if isinstance(block, TextContent):
            lines.extend(render_markdown(block.text, width))
        elif isinstance(block, ThinkingContent):
            lines.append(f"{DIM}Thinking{RESET}")
            lines.extend(
                f"{DIM}|{RESET} {line}"
                for line in render_markdown(block.thinking, max(1, width - 2))
            )
        elif isinstance(block, ToolCall):
            arguments = json.dumps(block.arguments, ensure_ascii=False, sort_keys=True)
            lines.extend(wrap_ansi(f"{DIM}> {block.name}{RESET} {arguments}", width))
    if message.error_message:
        lines.extend(wrap_ansi(f"Error: {message.error_message}", width))
    return lines or [f"{DIM}[empty assistant response]{RESET}"]


def _render_tool_result(
    message: ToolResultMessage,
    width: int,
    *,
    max_lines: int,
    expanded: bool,
) -> list[str]:
    content: list[str] = []
    for block in message.content:
        if isinstance(block, TextContent):
            content.extend(block.text.splitlines() or [""])
        elif isinstance(block, ImageContent):
            content.append(f"[image result: {block.mime_type}]")
    wrapped: list[str] = []
    for line in content:
        wrapped.extend(wrap_ansi(line, max(1, width - 2)))
    if not expanded and len(wrapped) > max_lines:
        omitted = len(wrapped) - max_lines
        wrapped = [*wrapped[:max_lines], f"{DIM}... {omitted} more lines{RESET}"]
    marker = "!" if message.is_error else "+"
    return [f"{DIM}{marker} {message.tool_name}{RESET}", *(f"  {line}" for line in wrapped)]


__all__ = [
    "CompletionOverlay",
    "Component",
    "MessageComponent",
    "Spacer",
    "StatusBar",
    "TextBlock",
    "ToolProgressComponent",
    "VBox",
]
