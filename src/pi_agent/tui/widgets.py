from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from .surface import Surface
from .types import KeyEvent, Rect, Style
from .width import pad_text, text_width, truncate_text

TranscriptRole: TypeAlias = Literal["user", "assistant", "tool", "system", "error"]


@dataclass(slots=True, frozen=True)
class TranscriptItem:
    role: TranscriptRole
    text: str
    details: str = ""

    def lines(self, width: int) -> list[str]:
        if width <= 0:
            return []
        label = {
            "user": "You",
            "assistant": "Assistant",
            "tool": "Tool",
            "system": "System",
            "error": "Error",
        }[self.role]
        prefix = f"{label}: "
        body_width = max(1, width - text_width(prefix))
        wrapped = _wrap(self.text, body_width)
        result = [prefix + wrapped[0]]
        result.extend(" " * text_width(prefix) + line for line in wrapped[1:])
        if self.details:
            result.extend("  " + line for line in _wrap(self.details, max(1, width - 2)))
        return result


@dataclass(slots=True)
class TranscriptView:
    items: list[TranscriptItem] = field(default_factory=list)
    offset: int = 0
    follow_tail: bool = True
    spacing: int = 1

    def append(self, item: TranscriptItem) -> None:
        self.items.append(item)
        if self.follow_tail:
            self.offset = 10**9

    def replace(self, index: int, item: TranscriptItem) -> None:
        if not 0 <= index < len(self.items):
            raise IndexError(index)
        self.items[index] = item

    def rendered_lines(self, width: int) -> list[str]:
        lines: list[str] = []
        for index, item in enumerate(self.items):
            if index and self.spacing:
                lines.extend("" for _ in range(self.spacing))
            lines.extend(item.lines(width))
        return lines

    def render(self, surface: Surface, rect: Rect) -> None:
        if rect.width <= 0 or rect.height <= 0:
            return
        lines = self.rendered_lines(rect.width)
        maximum = max(0, len(lines) - rect.height)
        if self.follow_tail:
            self.offset = maximum
        else:
            self.offset = min(max(0, self.offset), maximum)
        visible = lines[self.offset : self.offset + rect.height]
        for row, line in enumerate(visible):
            surface.draw_text(
                rect.x,
                rect.y + row,
                truncate_text(line, rect.width),
                max_width=rect.width,
            )

    def scroll(self, delta: int, *, viewport_height: int, width: int) -> None:
        lines = self.rendered_lines(max(1, width))
        maximum = max(0, len(lines) - max(0, viewport_height))
        current = maximum if self.follow_tail else self.offset
        self.offset = min(maximum, max(0, current + delta))
        self.follow_tail = self.offset >= maximum

    def to_tail(self) -> None:
        self.follow_tail = True
        self.offset = 10**9


@dataclass(slots=True, frozen=True)
class SelectOption:
    id: str
    label: str
    description: str = ""
    disabled: bool = False
    data: Any = None


@dataclass(slots=True)
class SelectList:
    options: list[SelectOption]
    title: str = "Select"
    selected: int = 0
    offset: int = 0
    border_style: Style = field(default_factory=lambda: Style(bold=True))
    selected_style: Style = field(default_factory=lambda: Style(reverse=True))

    def __post_init__(self) -> None:
        self.options = list(self.options)
        self._normalize_selected(1)

    def handle(self, event: KeyEvent) -> SelectOption | Literal["cancel"] | None:
        if event.key == "escape":
            return "cancel"
        if event.key in {"up", "down", "home", "end", "page_up", "page_down"}:
            step = {
                "up": -1,
                "down": 1,
                "home": -(10**9),
                "end": 10**9,
                "page_up": -10,
                "page_down": 10,
            }[event.key]
            if event.key == "home":
                self.selected = 0
                self._normalize_selected(1)
            elif event.key == "end":
                self.selected = max(0, len(self.options) - 1)
                self._normalize_selected(-1)
            else:
                self.selected = min(
                    max(0, self.selected + step),
                    max(0, len(self.options) - 1),
                )
                self._normalize_selected(1 if step >= 0 else -1)
            return None
        if event.key == "enter" and self.options:
            option = self.options[self.selected]
            return None if option.disabled else option
        if event.key == "character" and event.text:
            needle = event.text.casefold()
            for index in range(1, len(self.options) + 1):
                candidate = (self.selected + index) % len(self.options)
                option = self.options[candidate]
                if not option.disabled and option.label.casefold().startswith(needle):
                    self.selected = candidate
                    break
        return None

    def render(self, surface: Surface, rect: Rect) -> None:
        if rect.width <= 1 or rect.height <= 1:
            return
        surface.fill(rect)
        title = f" {truncate_text(self.title, max(0, rect.width - 4))} "
        surface.draw_text(rect.x, rect.y, "+" + "-" * max(0, rect.width - 2) + "+")
        if title and rect.width > 4:
            surface.draw_text(rect.x + 2, rect.y, title, style=self.border_style)
        for y in range(rect.y + 1, rect.bottom - 1):
            surface.draw_text(rect.x, y, "|")
            surface.draw_text(rect.right - 1, y, "|")
        surface.draw_text(rect.x, rect.bottom - 1, "+" + "-" * max(0, rect.width - 2) + "+")
        visible_height = max(0, rect.height - 2)
        if self.selected < self.offset:
            self.offset = self.selected
        elif self.selected >= self.offset + visible_height:
            self.offset = self.selected - visible_height + 1
        self.offset = min(max(0, self.offset), max(0, len(self.options) - visible_height))
        for row, option in enumerate(self.options[self.offset : self.offset + visible_height]):
            absolute = self.offset + row
            marker = "> " if absolute == self.selected else "  "
            label = marker + option.label
            if option.description:
                label += f"  {option.description}"
            style = self.selected_style if absolute == self.selected else Style(dim=option.disabled)
            surface.draw_text(
                rect.x + 1,
                rect.y + 1 + row,
                truncate_text(label, max(0, rect.width - 2), ellipsis="..."),
                style=style,
                max_width=max(0, rect.width - 2),
            )

    def _normalize_selected(self, direction: int) -> None:
        if not self.options:
            self.selected = 0
            return
        self.selected = min(max(0, self.selected), len(self.options) - 1)
        if not self.options[self.selected].disabled:
            return
        step = 1 if direction >= 0 else -1
        for _ in range(len(self.options)):
            self.selected = (self.selected + step) % len(self.options)
            if not self.options[self.selected].disabled:
                return


@dataclass(slots=True, frozen=True)
class StatusBar:
    left: str = ""
    center: str = ""
    right: str = ""
    style: Style = field(default_factory=lambda: Style(reverse=True))

    def render(self, surface: Surface, rect: Rect) -> None:
        if rect.width <= 0 or rect.height <= 0:
            return
        right = truncate_text(self.right, rect.width)
        left_budget = max(0, rect.width - text_width(right) - (1 if right else 0))
        left = truncate_text(self.left, left_budget, ellipsis="...")
        remaining = max(0, rect.width - text_width(left) - text_width(right))
        center = truncate_text(self.center, remaining, ellipsis="...")
        left_gap = max(0, (remaining - text_width(center)) // 2)
        right_gap = max(0, remaining - text_width(center) - left_gap)
        text = left + " " * left_gap + center + " " * right_gap + right
        surface.draw_text(
            rect.x,
            rect.y,
            pad_text(text, rect.width),
            style=self.style,
            max_width=rect.width,
        )


def _wrap(text: str, width: int) -> list[str]:
    if width <= 0:
        return []
    lines: list[str] = []
    for logical in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not logical:
            lines.append("")
            continue
        current = logical
        while text_width(current) > width:
            cut = width
            while cut > 0 and text_width(current[:cut]) > width:
                cut -= 1
            cut = max(1, cut)
            lines.append(current[:cut])
            current = current[cut:]
        lines.append(current)
    return lines or [""]


__all__ = ["SelectList", "SelectOption", "StatusBar", "TranscriptItem", "TranscriptView"]
