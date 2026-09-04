from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from .width import (
    cell_width,
    grapheme_width,
    graphemes,
    pad_cells,
    slice_text,
    strip_ansi,
    text_width,
    truncate_text,
    wrap_ansi,
)

Color: TypeAlias = int | tuple[int, int, int] | Literal["default"]
KeyName: TypeAlias = Literal[
    "character",
    "enter",
    "tab",
    "backtab",
    "backspace",
    "delete",
    "insert",
    "escape",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "page_up",
    "page_down",
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
    "ctrl_c",
    "ctrl_d",
    "ctrl_l",
    "ctrl_r",
    "unknown",
]


@dataclass(slots=True, frozen=True)
class Style:
    foreground: Color = "default"
    background: Color = "default"
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False

    def merge(self, overlay: Style) -> Style:
        return Style(
            foreground=(overlay.foreground if overlay.foreground != "default" else self.foreground),
            background=(overlay.background if overlay.background != "default" else self.background),
            bold=self.bold or overlay.bold,
            dim=self.dim or overlay.dim,
            italic=self.italic or overlay.italic,
            underline=self.underline or overlay.underline,
            reverse=self.reverse or overlay.reverse,
        )


@dataclass(slots=True, frozen=True)
class TextStyle:
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    inverse: bool = False
    foreground: int | None = None
    background: int | None = None

    def ansi_prefix(self) -> str:
        codes: list[str] = []
        if self.bold:
            codes.append("1")
        if self.dim:
            codes.append("2")
        if self.italic:
            codes.append("3")
        if self.underline:
            codes.append("4")
        if self.inverse:
            codes.append("7")
        if self.foreground is not None:
            codes.extend(("38", "5", str(self.foreground)))
        if self.background is not None:
            codes.extend(("48", "5", str(self.background)))
        return f"\x1b[{';'.join(codes)}m" if codes else ""


@dataclass(slots=True, frozen=True)
class StyledText:
    text: str
    style: TextStyle = field(default_factory=TextStyle)


@dataclass(slots=True, frozen=True)
class Point:
    x: int
    y: int


@dataclass(slots=True, frozen=True)
class Size:
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            raise ValueError("terminal size must be non-negative")


@dataclass(slots=True, frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            raise ValueError("rectangle dimensions must be non-negative")

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains(self, point: Point) -> bool:
        return self.x <= point.x < self.right and self.y <= point.y < self.bottom

    def intersect(self, other: Rect) -> Rect:
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        return Rect(left, top, max(0, right - left), max(0, bottom - top))


@dataclass(slots=True, frozen=True)
class Cell:
    text: str = " "
    style: Style = field(default_factory=Style)
    width: int = 1


@dataclass(slots=True, frozen=True)
class Cursor:
    x: int
    y: int
    visible: bool = True


@dataclass(slots=True, frozen=True)
class KeyEvent:
    key: KeyName
    text: str = ""
    alt: bool = False
    ctrl: bool = False
    shift: bool = False


@dataclass(slots=True, frozen=True, init=False)
class ResizeEvent:
    width: int
    height: int

    def __init__(self, width: int | Size, height: int | None = None) -> None:
        if isinstance(width, Size):
            actual_width = width.width
            actual_height = width.height
        else:
            if height is None:
                raise TypeError("height is required when width is an integer")
            actual_width = width
            actual_height = height
        if actual_width < 0 or actual_height < 0:
            raise ValueError("terminal size must be non-negative")
        object.__setattr__(self, "width", actual_width)
        object.__setattr__(self, "height", actual_height)

    @property
    def size(self) -> Size:
        return Size(self.width, self.height)


@dataclass(slots=True, frozen=True)
class PasteEvent:
    text: str


@dataclass(slots=True, frozen=True)
class MouseEvent:
    x: int
    y: int
    button: int
    action: Literal["press", "release", "drag", "scroll_up", "scroll_down"]
    shift: bool = False
    alt: bool = False
    ctrl: bool = False


@dataclass(slots=True, frozen=True)
class TickEvent:
    monotonic_time: float


@dataclass(slots=True, frozen=True)
class EndOfInputEvent:
    pass


TerminalEvent: TypeAlias = (
    KeyEvent | ResizeEvent | PasteEvent | MouseEvent | TickEvent | EndOfInputEvent
)


@dataclass(slots=True, frozen=True)
class Frame:
    width: int
    height: int
    lines: tuple[str, ...]
    cursor_x: int | None = None
    cursor_y: int | None = None
    cursor_visible: bool = True
    title: str | None = None

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame dimensions must be positive")
        if len(self.lines) > self.height:
            raise ValueError("frame has more lines than its height")
        if self.cursor_x is not None and not 0 <= self.cursor_x < self.width:
            raise ValueError("cursor_x is outside the frame")
        if self.cursor_y is not None and not 0 <= self.cursor_y < self.height:
            raise ValueError("cursor_y is outside the frame")


def slice_cells(value: str, max_width: int) -> str:
    return slice_text(strip_ansi(value), 0, max_width)


def truncate_cells(value: str, width: int, *, ellipsis: str = "…") -> str:
    return truncate_text(strip_ansi(value), width, ellipsis=ellipsis)


def wrap_cells(value: str, width: int) -> tuple[str, ...]:
    return tuple(wrap_ansi(value, width))


__all__ = [
    "Cell",
    "Color",
    "Cursor",
    "EndOfInputEvent",
    "Frame",
    "KeyEvent",
    "KeyName",
    "MouseEvent",
    "PasteEvent",
    "Point",
    "Rect",
    "ResizeEvent",
    "Size",
    "Style",
    "StyledText",
    "TerminalEvent",
    "TextStyle",
    "TickEvent",
    "cell_width",
    "grapheme_width",
    "graphemes",
    "pad_cells",
    "slice_cells",
    "strip_ansi",
    "text_width",
    "truncate_cells",
    "truncate_text",
    "wrap_cells",
]
