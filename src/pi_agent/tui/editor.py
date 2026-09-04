from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .types import KeyEvent, PasteEvent, TerminalEvent, cell_width, graphemes, wrap_cells

EditorActionKind = Literal["changed", "submit", "cancel", "exit", "clear_screen", "none"]


class EditorAction(str):
    @property
    def kind(self) -> EditorActionKind:
        return self  # type: ignore[return-value]


@dataclass(slots=True, frozen=True)
class EditorRender:
    lines: tuple[str, ...]
    cursor_x: int
    cursor_y: int


@dataclass(slots=True)
class TextEditor:
    text: str = ""
    cursor: int = 0
    history_limit: int = 200
    _history: list[str] = field(default_factory=list)
    _history_index: int | None = None
    _history_draft: str = ""
    _preferred_column: int | None = None

    def __post_init__(self) -> None:
        self.cursor = min(max(0, self.cursor), len(self.text))

    @property
    def history(self) -> tuple[str, ...]:
        return tuple(self._history)

    def set_text(self, value: str, *, cursor: int | None = None) -> None:
        self.text = value
        self.cursor = len(value) if cursor is None else min(max(0, cursor), len(value))
        self._preferred_column = None

    def insert(self, value: str) -> None:
        if not value:
            return
        self.text = self.text[: self.cursor] + value + self.text[self.cursor :]
        self.cursor += len(value)
        self._preferred_column = None
        self._history_index = None

    def backspace(self) -> None:
        start = _previous_grapheme_boundary(self.text, self.cursor)
        if start == self.cursor:
            return
        self.text = self.text[:start] + self.text[self.cursor :]
        self.cursor = start
        self._preferred_column = None
        self._history_index = None

    def delete(self) -> None:
        end = _next_grapheme_boundary(self.text, self.cursor)
        if end == self.cursor:
            return
        self.text = self.text[: self.cursor] + self.text[end:]
        self._preferred_column = None
        self._history_index = None

    def move_left(self) -> None:
        self.cursor = _previous_grapheme_boundary(self.text, self.cursor)
        self._preferred_column = None

    def move_right(self) -> None:
        self.cursor = _next_grapheme_boundary(self.text, self.cursor)
        self._preferred_column = None

    def move_home(self) -> None:
        self.cursor = self.text.rfind("\n", 0, self.cursor) + 1
        self._preferred_column = None

    def move_end(self) -> None:
        end = self.text.find("\n", self.cursor)
        self.cursor = len(self.text) if end < 0 else end
        self._preferred_column = None

    def move_word_left(self) -> None:
        index = self.cursor
        while index > 0 and self.text[index - 1].isspace():
            index -= 1
        while index > 0 and not self.text[index - 1].isspace():
            index -= 1
        self.cursor = index
        self._preferred_column = None

    def delete_word_left(self) -> None:
        original = self.cursor
        self.move_word_left()
        self.text = self.text[: self.cursor] + self.text[original:]
        self._history_index = None

    def move_vertical(self, direction: int) -> None:
        starts = _line_starts(self.text)
        line = _line_for_cursor(starts, self.cursor)
        column = self.cursor - starts[line]
        if self._preferred_column is None:
            self._preferred_column = column
        target = min(max(0, line + direction), len(starts) - 1)
        target_end = self.text.find("\n", starts[target])
        if target_end < 0:
            target_end = len(self.text)
        self.cursor = min(starts[target] + self._preferred_column, target_end)

    def previous_history(self) -> None:
        if not self._history:
            return
        if self._history_index is None:
            self._history_draft = self.text
            self._history_index = len(self._history) - 1
        else:
            self._history_index = max(0, self._history_index - 1)
        index = self._history_index
        self.set_text(self._history[index])
        self._history_index = index

    def next_history(self) -> None:
        if self._history_index is None:
            return
        if self._history_index >= len(self._history) - 1:
            self._history_index = None
            self.set_text(self._history_draft)
            return
        self._history_index += 1
        index = self._history_index
        self.set_text(self._history[index])
        self._history_index = index

    def remember(self, value: str) -> None:
        value = value.strip()
        if value and (not self._history or self._history[-1] != value):
            self._history.append(value)
            if len(self._history) > self.history_limit:
                del self._history[: len(self._history) - self.history_limit]

    def submit(self) -> str:
        value = self.text.strip()
        self.remember(value)
        self.set_text("")
        self._history_index = None
        self._history_draft = ""
        return value

    def handle(self, event: TerminalEvent) -> EditorAction:
        if isinstance(event, PasteEvent):
            self.insert(event.text.replace("\r\n", "\n").replace("\r", "\n"))
            return EditorAction("changed")
        if not isinstance(event, KeyEvent):
            return EditorAction("none")
        if event.key == "character":
            if event.ctrl:
                key = event.text.lower()
                if key == "a":
                    self.move_home()
                elif key == "e":
                    self.move_end()
                elif key == "b":
                    self.move_left()
                elif key == "f":
                    self.move_right()
                elif key == "w":
                    self.delete_word_left()
                elif key == "u":
                    start = self.text.rfind("\n", 0, self.cursor) + 1
                    self.text = self.text[:start] + self.text[self.cursor :]
                    self.cursor = start
                elif key == "k":
                    end = self.text.find("\n", self.cursor)
                    end = len(self.text) if end < 0 else end
                    self.text = self.text[: self.cursor] + self.text[end:]
                else:
                    return EditorAction("none")
                return EditorAction("changed")
            self.insert(event.text)
            return EditorAction("changed")
        if event.key == "backspace":
            self.backspace()
            return EditorAction("changed")
        if event.key == "delete":
            self.delete()
            return EditorAction("changed")
        if event.key == "left":
            self.move_left()
            return EditorAction("changed")
        if event.key == "right":
            self.move_right()
            return EditorAction("changed")
        if event.key == "home":
            self.move_home()
            return EditorAction("changed")
        if event.key == "end":
            self.move_end()
            return EditorAction("changed")
        if event.key == "up":
            if "\n" in self.text:
                self.move_vertical(-1)
            else:
                self.previous_history()
            return EditorAction("changed")
        if event.key == "down":
            if "\n" in self.text:
                self.move_vertical(1)
            else:
                self.next_history()
            return EditorAction("changed")
        if event.key == "enter":
            if event.shift or event.alt:
                self.insert("\n")
                return EditorAction("changed")
            return EditorAction("submit")
        if event.key in {"escape", "ctrl_c"}:
            return EditorAction("cancel")
        if event.key == "ctrl_d":
            return EditorAction("exit" if not self.text else "none")
        if event.key == "ctrl_l":
            return EditorAction("clear_screen")
        return EditorAction("none")

    def render(
        self,
        width: int,
        height: int | None = None,
        *,
        prompt: str = "> ",
    ) -> EditorRender:
        if width <= cell_width(prompt):
            raise ValueError("editor width is too small for the prompt")
        content_width = width - cell_width(prompt)
        before = self.text[: self.cursor]
        wrapped = list(wrap_cells(self.text, content_width)) or [""]
        before_lines = wrap_cells(before, content_width) or ("",)
        cursor_y = len(before_lines) - 1
        cursor_x = cell_width(prompt) + cell_width(before_lines[-1])
        lines = tuple(
            (prompt if index == 0 else " " * cell_width(prompt)) + line
            for index, line in enumerate(wrapped)
        )
        visible_lines = lines
        visible_cursor_y = cursor_y
        if height is not None:
            if height <= 0:
                return EditorRender((), 0, 0)
            start = max(0, cursor_y - height + 1)
            visible_lines = lines[start : start + height]
            visible_cursor_y = cursor_y - start
        return EditorRender(
            visible_lines,
            min(width - 1, cursor_x),
            min(max(0, visible_cursor_y), max(0, len(visible_lines) - 1)),
        )


def _grapheme_boundaries(value: str) -> list[int]:
    boundaries = [0]
    position = 0
    for cluster in graphemes(value):
        position += len(cluster)
        boundaries.append(position)
    return boundaries


def _previous_grapheme_boundary(value: str, cursor: int) -> int:
    return max((item for item in _grapheme_boundaries(value) if item < cursor), default=0)


def _next_grapheme_boundary(value: str, cursor: int) -> int:
    return next((item for item in _grapheme_boundaries(value) if item > cursor), cursor)


def _line_starts(value: str) -> list[int]:
    starts = [0]
    starts.extend(index + 1 for index, character in enumerate(value) if character == "\n")
    return starts


def _line_for_cursor(starts: list[int], cursor: int) -> int:
    for index in range(len(starts) - 1, -1, -1):
        if starts[index] <= cursor:
            return index
    return 0


__all__ = ["EditorAction", "EditorActionKind", "EditorRender", "TextEditor"]
