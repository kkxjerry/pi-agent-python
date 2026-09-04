from __future__ import annotations

import re
from typing import Literal

from .types import KeyEvent, MouseEvent, PasteEvent, TerminalEvent

_PASTE_START = b"\x1b[200~"
_PASTE_END = b"\x1b[201~"
_MOUSE = re.compile(rb"^\x1b\[<(\d+);(\d+);(\d+)([Mm])")

_SEQUENCES: dict[bytes, KeyEvent] = {
    b"\x1b[A": KeyEvent("up"),
    b"\x1b[B": KeyEvent("down"),
    b"\x1b[C": KeyEvent("right"),
    b"\x1b[D": KeyEvent("left"),
    b"\x1b[H": KeyEvent("home"),
    b"\x1b[F": KeyEvent("end"),
    b"\x1b[1~": KeyEvent("home"),
    b"\x1b[4~": KeyEvent("end"),
    b"\x1b[2~": KeyEvent("insert"),
    b"\x1b[3~": KeyEvent("delete"),
    b"\x1b[5~": KeyEvent("page_up"),
    b"\x1b[6~": KeyEvent("page_down"),
    b"\x1b[Z": KeyEvent("backtab", shift=True),
    b"\x1bOP": KeyEvent("f1"),
    b"\x1bOQ": KeyEvent("f2"),
    b"\x1bOR": KeyEvent("f3"),
    b"\x1bOS": KeyEvent("f4"),
    b"\x1b[15~": KeyEvent("f5"),
    b"\x1b[17~": KeyEvent("f6"),
    b"\x1b[18~": KeyEvent("f7"),
    b"\x1b[19~": KeyEvent("f8"),
    b"\x1b[20~": KeyEvent("f9"),
    b"\x1b[21~": KeyEvent("f10"),
    b"\x1b[23~": KeyEvent("f11"),
    b"\x1b[24~": KeyEvent("f12"),
    b"\x1b[1;2A": KeyEvent("up", shift=True),
    b"\x1b[1;2B": KeyEvent("down", shift=True),
    b"\x1b[1;2C": KeyEvent("right", shift=True),
    b"\x1b[1;2D": KeyEvent("left", shift=True),
    b"\x1b[1;3C": KeyEvent("right", alt=True),
    b"\x1b[1;3D": KeyEvent("left", alt=True),
    b"\x1b[1;5C": KeyEvent("right", ctrl=True),
    b"\x1b[1;5D": KeyEvent("left", ctrl=True),
}


class InputDecoder:
    """Incremental terminal decoder with bracketed-paste and SGR mouse support."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._paste = bytearray()
        self._pasting = False

    @property
    def has_pending_escape(self) -> bool:
        return bool(self._buffer) and self._buffer[0] == 0x1B and not self._pasting

    def feed(self, data: bytes) -> tuple[TerminalEvent, ...]:
        self._buffer.extend(data)
        events: list[TerminalEvent] = []
        while self._buffer:
            if self._pasting:
                end = self._buffer.find(_PASTE_END)
                if end < 0:
                    keep = max(0, len(self._buffer) - len(_PASTE_END) + 1)
                    self._paste.extend(self._buffer[:keep])
                    del self._buffer[:keep]
                    break
                self._paste.extend(self._buffer[:end])
                del self._buffer[: end + len(_PASTE_END)]
                events.append(PasteEvent(self._paste.decode("utf-8", errors="replace")))
                self._paste.clear()
                self._pasting = False
                continue
            if self._buffer.startswith(_PASTE_START):
                del self._buffer[: len(_PASTE_START)]
                self._pasting = True
                continue
            if self._buffer[0] == 0x1B:
                escape_result = self._parse_escape()
                if escape_result is None:
                    break
                escape_event, consumed = escape_result
                del self._buffer[:consumed]
                events.append(escape_event)
                continue
            character_result = _decode_character(bytes(self._buffer))
            if character_result is None:
                break
            character, consumed = character_result
            del self._buffer[:consumed]
            events.append(_character_event(character))
        return tuple(events)

    def flush_escape(self) -> tuple[TerminalEvent, ...]:
        """Resolve ambiguous partial input after the host's escape timeout."""

        events: list[TerminalEvent] = []
        if self._pasting:
            self._paste.extend(self._buffer)
            self._buffer.clear()
            payload = self._paste.decode("utf-8", errors="replace")
            self._paste.clear()
            self._pasting = False
            if payload:
                events.append(PasteEvent(payload))
            return tuple(events)
        while self._buffer:
            if self._buffer[0] == 0x1B:
                escape_result = self._parse_escape(force=True)
                assert escape_result is not None
                escape_event, consumed = escape_result
                del self._buffer[:consumed]
                events.append(escape_event)
                continue
            character_result = _decode_character(bytes(self._buffer), force=True)
            assert character_result is not None
            character, consumed = character_result
            del self._buffer[:consumed]
            events.append(_character_event(character))
        return tuple(events)

    def finish(self) -> tuple[TerminalEvent, ...]:
        return self.flush_escape()

    def _parse_escape(self, *, force: bool = False) -> tuple[TerminalEvent, int] | None:
        data = bytes(self._buffer)
        mouse_match = _MOUSE.match(data)
        if mouse_match is not None:
            code = int(mouse_match.group(1))
            x = max(0, int(mouse_match.group(2)) - 1)
            y = max(0, int(mouse_match.group(3)) - 1)
            return _mouse_event(code, x, y, mouse_match.group(4) == b"m"), mouse_match.end()
        for sequence in sorted(_SEQUENCES, key=len, reverse=True):
            if data.startswith(sequence):
                return _SEQUENCES[sequence], len(sequence)
        incomplete_known = any(sequence.startswith(data) for sequence in _SEQUENCES)
        if (incomplete_known or _PASTE_START.startswith(data)) and not force:
            return None
        if len(data) == 1:
            return (KeyEvent("escape"), 1) if force else None
        alt_result = _decode_character(data[1:])
        if data.startswith(b"\x1b") and alt_result is not None:
            character, consumed = alt_result
            event = _character_event(character)
            if event.key == "character":
                event = KeyEvent(
                    "character",
                    event.text,
                    ctrl=event.ctrl,
                    alt=True,
                    shift=event.shift,
                )
            return event, consumed + 1
        if data.startswith((b"\x1b[", b"\x1bO")):
            terminator = _find_escape_terminator(data)
            if terminator is None and not force:
                return None
            if terminator is not None:
                return KeyEvent("unknown", text=data[:terminator].decode("latin-1")), terminator
        return KeyEvent("escape"), 1


InputParser = InputDecoder


def _decode_character(data: bytes, *, force: bool = False) -> tuple[str, int] | None:
    if not data:
        return None
    first = data[0]
    if first < 0x80:
        return chr(first), 1
    length = 2 if first < 0xE0 else 3 if first < 0xF0 else 4 if first < 0xF8 else 1
    if len(data) < length and not force:
        return None
    payload = data[: min(length, len(data))]
    try:
        return payload.decode("utf-8"), len(payload)
    except UnicodeDecodeError:
        if len(data) < length and not force:
            return None
        return "\ufffd", 1


def _character_event(character: str) -> KeyEvent:
    codepoint = ord(character)
    if character in {"\r", "\n"}:
        return KeyEvent("enter")
    if character == "\t":
        return KeyEvent("tab")
    if character in {"\x7f", "\b"}:
        return KeyEvent("backspace")
    if character == "\x1b":
        return KeyEvent("escape")
    if 1 <= codepoint <= 26:
        return KeyEvent("character", chr(ord("a") + codepoint - 1), ctrl=True)
    if codepoint == 0:
        return KeyEvent("character", "@", ctrl=True)
    if codepoint < 32:
        return KeyEvent("unknown", text=character)
    return KeyEvent("character", character)


def _find_escape_terminator(data: bytes) -> int | None:
    for index, byte in enumerate(data[2:], start=2):
        if 0x40 <= byte <= 0x7E:
            return index + 1
    return None


def _mouse_event(code: int, x: int, y: int, release: bool) -> MouseEvent:
    shift = bool(code & 4)
    alt = bool(code & 8)
    ctrl = bool(code & 16)
    base = code & 0b11
    action: Literal["press", "release", "drag", "scroll_up", "scroll_down"]
    if code & 64:
        action = "scroll_up" if base == 0 else "scroll_down"
        button = 4 if action == "scroll_up" else 5
    elif release:
        action = "release"
        button = base
    elif code & 32:
        action = "drag"
        button = base
    else:
        action = "press"
        button = base
    return MouseEvent(x, y, button, action, shift=shift, alt=alt, ctrl=ctrl)
