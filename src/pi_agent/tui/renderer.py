from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .surface import Surface
from .types import Cell, Color, Cursor, Style

WriteText = Callable[[str], None]


@dataclass(slots=True, frozen=True)
class RenderStats:
    changed_rows: int
    changed_cells: int
    bytes_written: int
    full_redraw: bool


class AnsiRenderer:
    """Render a Surface with row-bounded diffs and deterministic style resets."""

    def __init__(self, writer: WriteText, *, synchronized_output: bool = False) -> None:
        self.writer = writer
        self.synchronized_output = synchronized_output
        self._previous: Surface | None = None
        self._cursor: Cursor | None = None

    def reset(self) -> None:
        self._previous = None
        self._cursor = None

    def render(self, surface: Surface, cursor: Cursor | None = None) -> RenderStats:
        diffs = surface.diff(self._previous)
        full = self._previous is None or self._previous.size != surface.size
        chunks: list[str] = []
        changed_cells = 0
        for diff in diffs:
            chunks.append(_move(diff.row + 1, diff.start + 1))
            current_style: Style | None = None
            cells = surface.row(diff.row)
            for x in range(diff.start, diff.end):
                cell = cells[x]
                if cell.width == 0:
                    continue
                if cell.style != current_style:
                    chunks.append(_style(cell.style))
                    current_style = cell.style
                chunks.append(cell.text or " ")
                changed_cells += max(1, cell.width)
            chunks.append("\x1b[0m")
        if cursor is None or not cursor.visible:
            chunks.append("\x1b[?25l")
        else:
            chunks.append("\x1b[?25h")
            chunks.append(_move(cursor.y + 1, cursor.x + 1))
        payload = "".join(chunks)
        if payload and self.synchronized_output:
            payload = "\x1b[?2026h" + payload + "\x1b[?2026l"
        if payload:
            self.writer(payload)
        self._previous = surface.clone()
        self._cursor = cursor
        return RenderStats(len(diffs), changed_cells, len(payload.encode("utf-8")), full)


def _move(row: int, column: int) -> str:
    return f"\x1b[{max(1, row)};{max(1, column)}H"


def _style(style: Style) -> str:
    codes: list[str] = ["0"]
    if style.bold:
        codes.append("1")
    if style.dim:
        codes.append("2")
    if style.italic:
        codes.append("3")
    if style.underline:
        codes.append("4")
    if style.reverse:
        codes.append("7")
    codes.extend(_color_codes(style.foreground, foreground=True))
    codes.extend(_color_codes(style.background, foreground=False))
    return f"\x1b[{';'.join(codes)}m"


def _color_codes(color: Color, *, foreground: bool) -> list[str]:
    if color == "default":
        return ["39" if foreground else "49"]
    if isinstance(color, int):
        value = max(0, min(255, color))
        return ["38" if foreground else "48", "5", str(value)]
    red, green, blue = (max(0, min(255, component)) for component in color)
    return ["38" if foreground else "48", "2", str(red), str(green), str(blue)]


def render_cells(cells: tuple[Cell, ...]) -> str:
    """Render a row fragment; useful for snapshot tests and non-terminal hosts."""

    chunks: list[str] = []
    current: Style | None = None
    for cell in cells:
        if cell.width == 0:
            continue
        if cell.style != current:
            chunks.append(_style(cell.style))
            current = cell.style
        chunks.append(cell.text or " ")
    chunks.append("\x1b[0m")
    return "".join(chunks)
