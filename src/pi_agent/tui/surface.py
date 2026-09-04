from __future__ import annotations

import copy
from dataclasses import dataclass

from .types import Cell, Rect, Size, Style
from .width import grapheme_width, graphemes


@dataclass(slots=True, frozen=True)
class SurfaceDiff:
    row: int
    start: int
    end: int


class Surface:
    """A clipped terminal cell grid with explicit wide-cell continuations."""

    def __init__(self, width: int, height: int, *, default_style: Style | None = None) -> None:
        self.size = Size(width, height)
        self.default_style = default_style or Style()
        self._rows: list[list[Cell]] = [
            [Cell(style=self.default_style) for _ in range(width)] for _ in range(height)
        ]

    @property
    def width(self) -> int:
        return self.size.width

    @property
    def height(self) -> int:
        return self.size.height

    def clone(self) -> Surface:
        result = Surface(self.width, self.height, default_style=self.default_style)
        result._rows = copy.deepcopy(self._rows)
        return result

    def clear(self, style: Style | None = None) -> None:
        selected = style or self.default_style
        self._rows = [[Cell(style=selected) for _ in range(self.width)] for _ in range(self.height)]

    def cell(self, x: int, y: int) -> Cell:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise IndexError((x, y))
        return self._rows[y][x]

    def set_cell(self, x: int, y: int, cell: Cell) -> None:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            return
        self._erase_wide_neighbor(x, y)
        width = max(0, min(2, cell.width))
        if width == 2 and x + 1 >= self.width:
            return
        if width == 2:
            self._erase_wide_neighbor(x + 1, y)
        self._rows[y][x] = Cell(cell.text, cell.style, width)
        if width == 2:
            self._rows[y][x + 1] = Cell("", cell.style, 0)

    def fill(self, rect: Rect, *, text: str = " ", style: Style | None = None) -> None:
        selected = style or self.default_style
        clipped = rect.intersect(Rect(0, 0, self.width, self.height))
        for y in range(clipped.y, clipped.bottom):
            for x in range(clipped.x, clipped.right):
                self.set_cell(x, y, Cell(text, selected, 1))

    def draw_text(
        self,
        x: int,
        y: int,
        text: str,
        *,
        style: Style | None = None,
        max_width: int | None = None,
    ) -> int:
        if y < 0 or y >= self.height:
            return 0
        selected = style or self.default_style
        column = x
        origin = x
        limit = self.width if max_width is None else min(self.width, x + max_width)
        for cluster in graphemes(text):
            if cluster in {"\n", "\r"}:
                break
            width = grapheme_width(cluster)
            if width <= 0:
                continue
            if column + width > limit:
                break
            if column >= 0:
                self.set_cell(column, y, Cell(cluster, selected, width))
            column += width
        return max(0, column - origin)

    def draw_lines(
        self,
        x: int,
        y: int,
        lines: list[str] | tuple[str, ...],
        *,
        style: Style | None = None,
        max_width: int | None = None,
        max_height: int | None = None,
    ) -> None:
        limit = len(lines) if max_height is None else min(len(lines), max_height)
        for offset, line in enumerate(lines[:limit]):
            self.draw_text(x, y + offset, line, style=style, max_width=max_width)

    def blit(self, source: Surface, x: int, y: int, *, source_rect: Rect | None = None) -> None:
        rect = source_rect or Rect(0, 0, source.width, source.height)
        clipped = rect.intersect(Rect(0, 0, source.width, source.height))
        for source_y in range(clipped.y, clipped.bottom):
            target_y = y + source_y - clipped.y
            if not 0 <= target_y < self.height:
                continue
            for source_x in range(clipped.x, clipped.right):
                target_x = x + source_x - clipped.x
                cell = source.cell(source_x, source_y)
                if cell.width == 0:
                    continue
                self.set_cell(target_x, target_y, copy.deepcopy(cell))

    def crop(self, rect: Rect) -> Surface:
        result = Surface(rect.width, rect.height, default_style=self.default_style)
        for y in range(rect.height):
            for x in range(rect.width):
                source_x = rect.x + x
                source_y = rect.y + y
                if 0 <= source_x < self.width and 0 <= source_y < self.height:
                    cell = self.cell(source_x, source_y)
                    if cell.width != 0:
                        result.set_cell(x, y, copy.deepcopy(cell))
        return result

    def plain_lines(self) -> tuple[str, ...]:
        rows: list[str] = []
        for row in self._rows:
            rows.append("".join(cell.text for cell in row if cell.width != 0))
        return tuple(rows)

    def diff(self, previous: Surface | None) -> tuple[SurfaceDiff, ...]:
        if previous is None or previous.size != self.size:
            return tuple(SurfaceDiff(y, 0, self.width) for y in range(self.height))
        result: list[SurfaceDiff] = []
        for y in range(self.height):
            first: int | None = None
            last = 0
            for x, cell in enumerate(self._rows[y]):
                if cell != previous._rows[y][x]:
                    first = x if first is None else first
                    last = x + 1
            if first is not None:
                while first > 0 and self._rows[y][first].width == 0:
                    first -= 1
                while last < self.width and self._rows[y][last].width == 0:
                    last += 1
                result.append(SurfaceDiff(y, first, last))
        return tuple(result)

    def row(self, y: int) -> tuple[Cell, ...]:
        if not 0 <= y < self.height:
            raise IndexError(y)
        return tuple(self._rows[y])

    def _erase_wide_neighbor(self, x: int, y: int) -> None:
        current = self._rows[y][x]
        if current.width == 0 and x > 0:
            self._rows[y][x - 1] = Cell(style=self.default_style)
        elif current.width == 2 and x + 1 < self.width:
            self._rows[y][x + 1] = Cell(style=self.default_style)
