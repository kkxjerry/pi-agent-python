"""Line-oriented compatibility renderer built on the consolidated TUI types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import overload

from .renderer import AnsiRenderer as _SurfaceRenderer
from .renderer import RenderStats
from .surface import Surface
from .types import Frame as LineFrame
from .types import Size
from .width import pad_cells, slice_text, text_width

SYNC_BEGIN = "\x1b[?2026h"
SYNC_END = "\x1b[?2026l"


class Canvas:
    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("canvas dimensions must be positive")
        self.width = width
        self.height = height
        self._lines = [" " * width for _ in range(height)]

    def clear(self) -> None:
        self._lines = [" " * self.width for _ in range(self.height)]

    def draw_text(self, x: int, y: int, value: str) -> None:
        if not 0 <= y < self.height or x >= self.width:
            return
        start = max(0, x)
        visible = value if x >= 0 else slice_text(value, -x, self.width)
        visible = slice_text(visible, 0, max(0, self.width - start))
        if not visible:
            return
        line = self._lines[y]
        end = min(self.width, start + text_width(visible))
        prefix = slice_text(line, 0, start)
        suffix = slice_text(line, end, self.width - end)
        self._lines[y] = pad_cells(prefix + visible + suffix, self.width)

    def frame(
        self,
        *,
        cursor_x: int | None = None,
        cursor_y: int | None = None,
        cursor_visible: bool = True,
        title: str | None = None,
    ) -> LineFrame:
        return LineFrame(
            self.width,
            self.height,
            tuple(self._lines),
            cursor_x=cursor_x,
            cursor_y=cursor_y,
            cursor_visible=cursor_visible,
            title=title,
        )


class AnsiRenderer:
    """Render a line Frame or delegate a Surface to the cell renderer."""

    def __init__(
        self,
        writer: Callable[[str], None] | None = None,
        *,
        synchronized_output: bool = True,
    ) -> None:
        self.synchronized_output = synchronized_output
        self._previous_line_frame: LineFrame | None = None
        self._surface = (
            _SurfaceRenderer(writer, synchronized_output=synchronized_output)
            if writer is not None
            else None
        )

    def enter(self, *, alternate_screen: bool = True) -> str:
        self.invalidate()
        return ("\x1b[?1049h" if alternate_screen else "") + "\x1b[?25l\x1b[2J\x1b[H"

    def exit(self, *, alternate_screen: bool = True) -> str:
        self.invalidate()
        return "\x1b[0m\x1b[?25h" + ("\x1b[?1049l" if alternate_screen else "")

    def invalidate(self) -> None:
        self._previous_line_frame = None
        if self._surface is not None:
            self._surface.reset()

    @overload
    def render(self, value: LineFrame) -> str: ...

    @overload
    def render(self, value: Surface) -> RenderStats: ...

    def render(self, value: LineFrame | Surface) -> str | RenderStats:
        if isinstance(value, Surface):
            if self._surface is None:
                raise TypeError("Surface rendering requires a writer")
            return self._surface.render(value)
        return self._render_line_frame(value)

    def _render_line_frame(self, frame: LineFrame) -> str:
        previous = self._previous_line_frame
        force = previous is None or previous.width != frame.width or previous.height != frame.height
        old_lines = previous.lines if previous is not None and not force else ()
        chunks: list[str] = [SYNC_BEGIN] if self.synchronized_output else []
        if force:
            chunks.append("\x1b[2J")
        lines = list(frame.lines[: frame.height])
        lines.extend("" for _ in range(frame.height - len(lines)))
        normalized = tuple(pad_cells(line, frame.width) for line in lines)
        for index, line in enumerate(normalized):
            old = old_lines[index] if index < len(old_lines) else None
            if force or line != old:
                chunks.append(f"\x1b[{index + 1};1H\x1b[2K{line}")
        if frame.title is not None:
            chunks.append(f"\x1b]0;{frame.title}\x07")
        if frame.cursor_visible and frame.cursor_x is not None and frame.cursor_y is not None:
            chunks.append(f"\x1b[?25h\x1b[{frame.cursor_y + 1};{frame.cursor_x + 1}H")
        else:
            chunks.append("\x1b[?25l")
        if self.synchronized_output:
            chunks.append(SYNC_END)
        self._previous_line_frame = LineFrame(
            frame.width,
            frame.height,
            normalized,
            cursor_x=frame.cursor_x,
            cursor_y=frame.cursor_y,
            cursor_visible=frame.cursor_visible,
            title=frame.title,
        )
        return "".join(chunks)


@dataclass(slots=True, frozen=True)
class Cursor:
    row: int
    column: int
    visible: bool = True


@dataclass(slots=True, frozen=True)
class Frame:
    lines: tuple[str, ...]
    cursor: Cursor | None = None

    @classmethod
    def from_lines(
        cls,
        lines: list[str] | tuple[str, ...],
        cursor: Cursor | None = None,
    ) -> Frame:
        return cls(tuple(lines), cursor)


class ScreenRenderer:
    def __init__(self, *, synchronized: bool = True) -> None:
        self.synchronized = synchronized
        self._previous: Frame | None = None
        self._size: Size | None = None

    def reset(self) -> None:
        self._previous = None
        self._size = None

    def render(self, frame: Frame, size: Size) -> str:
        normalized = normalize_frame(frame, size)
        force = self._previous is None or self._size != size
        old_lines = self._previous.lines if self._previous is not None and not force else ()
        chunks: list[str] = [SYNC_BEGIN] if self.synchronized else []
        if force:
            chunks.append("\x1b[2J")
        for index, line in enumerate(normalized.lines):
            previous = old_lines[index] if index < len(old_lines) else None
            if force or line != previous:
                chunks.append(f"\x1b[{index + 1};1H\x1b[2K{line}")
        cursor = normalized.cursor
        if cursor is None or not cursor.visible:
            chunks.append("\x1b[?25l")
        else:
            chunks.append("\x1b[?25h")
            chunks.append(f"\x1b[{cursor.row + 1};{cursor.column + 1}H")
        if self.synchronized:
            chunks.append(SYNC_END)
        self._previous = normalized
        self._size = size
        return "".join(chunks)


@dataclass(slots=True)
class Viewport:
    offset: int = 0
    follow_tail: bool = True

    def visible(self, lines: list[str] | tuple[str, ...], height: int) -> tuple[str, ...]:
        if height <= 0:
            return ()
        maximum = max(0, len(lines) - height)
        if self.follow_tail:
            self.offset = maximum
        else:
            self.offset = min(max(0, self.offset), maximum)
        return tuple(lines[self.offset : self.offset + height])

    def scroll(self, delta: int, *, line_count: int, height: int) -> None:
        maximum = max(0, line_count - max(0, height))
        current = maximum if self.follow_tail else self.offset
        self.offset = min(maximum, max(0, current + delta))
        self.follow_tail = self.offset >= maximum

    def page(self, direction: int, *, line_count: int, height: int) -> None:
        self.scroll(direction * max(1, height - 1), line_count=line_count, height=height)

    def to_tail(self) -> None:
        self.follow_tail = True


def normalize_frame(frame: Frame, size: Size) -> Frame:
    lines = list(frame.lines[: size.height])
    lines.extend("" for _ in range(size.height - len(lines)))
    normalized = tuple(pad_cells(line, size.width) for line in lines)
    cursor = frame.cursor
    if cursor is not None and size.width and size.height:
        cursor = Cursor(
            row=min(max(0, cursor.row), size.height - 1),
            column=min(max(0, cursor.column), size.width - 1),
            visible=cursor.visible,
        )
    return Frame(normalized, cursor)


__all__ = [
    "SYNC_BEGIN",
    "SYNC_END",
    "AnsiRenderer",
    "Canvas",
    "Cursor",
    "Frame",
    "ScreenRenderer",
    "Viewport",
    "normalize_frame",
]
