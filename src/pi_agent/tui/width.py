from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

_ANSI_PATTERN = re.compile(r"(?:\x1b\][^\x07\x1b]*(?:\x07|\x1b\\))|(?:\x1b\[[0-?]*[ -/]*[@-~])")

_ZWJ = "\u200d"
_VARIATION_SELECTORS = range(0xFE00, 0xFE10)
_SUPPLEMENTARY_VARIATION_SELECTORS = range(0xE0100, 0xE01F0)
_EMOJI_MODIFIERS = range(0x1F3FB, 0x1F400)
_REGIONAL_INDICATORS = range(0x1F1E6, 0x1F200)


def graphemes(text: str) -> Iterator[str]:
    """Yield practical terminal grapheme clusters without a third-party runtime.

    This handles combining marks, variation selectors, emoji modifiers, ZWJ
    sequences, and regional-indicator flags. It is intentionally terminal-
    oriented rather than a complete Unicode text-segmentation implementation.
    """

    cluster = ""
    regional_count = 0
    for character in text:
        codepoint = ord(character)
        combining = bool(unicodedata.combining(character))
        variation = (
            codepoint in _VARIATION_SELECTORS or codepoint in _SUPPLEMENTARY_VARIATION_SELECTORS
        )
        modifier = codepoint in _EMOJI_MODIFIERS
        regional = codepoint in _REGIONAL_INDICATORS
        extend = bool(cluster) and (
            combining
            or variation
            or modifier
            or character == _ZWJ
            or cluster.endswith(_ZWJ)
            or (regional and regional_count == 1)
        )
        if not cluster or extend:
            cluster += character
        else:
            yield cluster
            cluster = character
            regional_count = 0
        if regional:
            regional_count += 1
        elif not variation and not combining and not modifier and character != _ZWJ:
            regional_count = 0
    if cluster:
        yield cluster


def grapheme_width(cluster: str) -> int:
    if not cluster:
        return 0
    if _ZWJ in cluster or any(ord(character) in _REGIONAL_INDICATORS for character in cluster):
        return 2
    if any(_is_emoji(character) for character in cluster):
        return 2
    width = 0
    for character in cluster:
        codepoint = ord(character)
        if character in {"\n", "\r", "\t"}:
            continue
        if (
            unicodedata.combining(character)
            or codepoint in _VARIATION_SELECTORS
            or codepoint in _SUPPLEMENTARY_VARIATION_SELECTORS
            or codepoint in _EMOJI_MODIFIERS
        ):
            continue
        category = unicodedata.category(character)
        if category.startswith("C"):
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
    return width


def text_width(text: str, *, tab_size: int = 4, start_column: int = 0) -> int:
    column = start_column
    initial = column
    for cluster in graphemes(text):
        if cluster == "\t":
            column += tab_size - (column % tab_size)
        elif cluster in {"\n", "\r"}:
            column = 0
        else:
            column += grapheme_width(cluster)
    return column - initial if "\n" not in text and "\r" not in text else column


def truncate_text(text: str, width: int, *, ellipsis: str = "") -> str:
    if width <= 0:
        return ""
    ellipsis_width = text_width(ellipsis)
    if ellipsis_width > width:
        return slice_text(ellipsis, 0, width)
    target = width - ellipsis_width
    used = 0
    result: list[str] = []
    truncated = False
    for cluster in graphemes(text):
        cluster_width = grapheme_width(cluster)
        if used + cluster_width > target:
            truncated = True
            break
        result.append(cluster)
        used += cluster_width
    if not truncated:
        return "".join(result)
    return "".join(result) + ellipsis


def slice_text(text: str, start: int, width: int) -> str:
    if start < 0 or width < 0:
        raise ValueError("start and width must be non-negative")
    end = start + width
    column = 0
    result: list[str] = []
    for cluster in graphemes(text):
        cluster_width = grapheme_width(cluster)
        next_column = column + cluster_width
        if next_column <= start:
            column = next_column
            continue
        if column >= end or next_column > end:
            break
        result.append(cluster)
        column = next_column
    return "".join(result)


def pad_text(text: str, width: int, *, align: str = "left") -> str:
    clipped = truncate_text(text, width)
    padding = max(0, width - text_width(clipped))
    if align == "right":
        return " " * padding + clipped
    if align == "center":
        left = padding // 2
        return " " * left + clipped + " " * (padding - left)
    if align != "left":
        raise ValueError("align must be left, center, or right")
    return clipped + " " * padding


def strip_ansi(text: str) -> str:
    return _ANSI_PATTERN.sub("", text)


def character_width(character: str) -> int:
    return grapheme_width(character)


def cell_width(text: str) -> int:
    return text_width(strip_ansi(text))


def clip_ansi(text: str, width: int, *, ellipsis: str = "") -> str:
    plain = strip_ansi(text)
    return truncate_text(plain, width, ellipsis=ellipsis)


def pad_cells(text: str, width: int) -> str:
    plain = clip_ansi(text, width)
    return plain + " " * max(0, width - cell_width(plain))


def wrap_ansi(text: str, width: int) -> list[str]:
    if width <= 0:
        raise ValueError("wrap width must be positive")
    result: list[str] = []
    for logical_line in strip_ansi(text).splitlines() or [""]:
        current: list[str] = []
        used = 0
        for cluster in graphemes(logical_line):
            cluster_width = grapheme_width(cluster)
            if current and used + cluster_width > width:
                result.append("".join(current))
                current = []
                used = 0
            current.append(cluster)
            used += cluster_width
        result.append("".join(current))
    return result or [""]


def _is_emoji(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or codepoint in {0x00A9, 0x00AE, 0x203C, 0x2049, 0x3030, 0x303D, 0x3297, 0x3299}
    )
