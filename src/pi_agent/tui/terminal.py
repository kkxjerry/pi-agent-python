from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Generator, Iterator
from dataclasses import dataclass, field
from typing import BinaryIO, Protocol, TextIO

from .types import EndOfInputEvent, KeyEvent, PasteEvent, ResizeEvent, Size, TerminalEvent


@dataclass(slots=True, frozen=True)
class TerminalCapabilities:
    color_depth: int = 256
    true_color: bool = False
    unicode: bool = True
    mouse: bool = False
    bracketed_paste: bool = True
    alternate_screen: bool = True
    kitty_images: bool = False
    iterm_images: bool = False


class Terminal(Protocol):
    @property
    def is_tty(self) -> bool: ...

    def size(self) -> tuple[int, int]: ...

    async def read_event(self) -> TerminalEvent: ...

    def write(self, value: str) -> Awaitable[None]: ...

    def flush(self) -> None: ...

    def raw_mode(self) -> contextlib.AbstractContextManager[None]: ...


@dataclass(slots=True)
class KeyDecoder:
    escape_timeout: float = 0.03
    _buffer: bytearray = field(default_factory=bytearray)
    _events: deque[TerminalEvent] = field(default_factory=deque)
    _paste: bytearray | None = None

    def feed(self, data: bytes) -> tuple[TerminalEvent, ...]:
        self._buffer.extend(data)
        self._decode()
        events = tuple(self._events)
        self._events.clear()
        return events

    def flush_escape(self) -> tuple[TerminalEvent, ...]:
        if not self._buffer or self._buffer[0] != 0x1B:
            return ()
        self._decode(final=True)
        events = tuple(self._events)
        self._events.clear()
        return events

    def end(self) -> tuple[TerminalEvent, ...]:
        self._decode(final=True)
        events = tuple(self._events)
        self._events.clear()
        return (*events, EndOfInputEvent())

    def _decode(self, *, final: bool = False) -> None:
        while self._buffer:
            if self._paste is not None:
                marker = self._buffer.find(b"\x1b[201~")
                if marker < 0:
                    if final:
                        self._paste.extend(self._buffer)
                        self._buffer.clear()
                        self._events.append(
                            PasteEvent(self._paste.decode("utf-8", errors="replace"))
                        )
                        self._paste = None
                    elif len(self._buffer) > 6:
                        self._paste.extend(self._buffer[:-6])
                        del self._buffer[:-6]
                    return
                self._paste.extend(self._buffer[:marker])
                del self._buffer[: marker + 6]
                self._events.append(PasteEvent(self._paste.decode("utf-8", errors="replace")))
                self._paste = None
                continue

            if self._buffer.startswith(b"\x1b[200~"):
                del self._buffer[:6]
                self._paste = bytearray()
                continue
            sequence = _matching_escape(self._buffer)
            if sequence is not None:
                consumed, event = sequence
                del self._buffer[:consumed]
                self._events.append(event)
                continue
            if self._buffer[0] == 0x1B and _is_escape_prefix(self._buffer) and not final:
                return
            if self._buffer[0] == 0x1B:
                if len(self._buffer) == 1 and not final:
                    return
                if len(self._buffer) >= 2 and self._buffer[1] not in {ord("["), ord("O")}:
                    character = bytes((self._buffer[1],)).decode("utf-8", errors="replace")
                    del self._buffer[:2]
                    self._events.append(KeyEvent("character", character, alt=True))
                    continue
                del self._buffer[:1]
                self._events.append(KeyEvent("escape"))
                continue
            byte = self._buffer[0]
            control = _control_event(byte)
            if control is not None:
                del self._buffer[:1]
                self._events.append(control)
                continue
            length = _utf8_length(byte)
            if len(self._buffer) < length and not final:
                return
            encoded = bytes(self._buffer[:length])
            del self._buffer[:length]
            text = encoded.decode("utf-8", errors="replace")
            self._events.append(KeyEvent("character", text))


class _CompletedWrite:
    def __await__(self) -> Generator[None, None, None]:
        if False:
            yield None
        return None


class OutputBuffer(list[str]):
    def __call__(self) -> str:
        return "".join(self)


class StdioTerminal:
    def __init__(
        self,
        stdin: BinaryIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout
        self.decoder = KeyDecoder()
        self._pending: deque[TerminalEvent] = deque()
        self.capabilities = detect_terminal_capabilities(self.stdout)

    @property
    def is_tty(self) -> bool:
        return bool(self.stdin.isatty() and self.stdout.isatty())

    def size(self) -> tuple[int, int]:
        size = shutil.get_terminal_size((80, 24))
        return max(20, size.columns), max(6, size.lines)

    async def read_event(self) -> TerminalEvent:
        if self._pending:
            return self._pending.popleft()
        escape_pending = False
        while not self._pending:
            try:
                data = await asyncio.wait_for(
                    asyncio.to_thread(self.stdin.read, 1),
                    timeout=self.decoder.escape_timeout if escape_pending else None,
                )
            except TimeoutError:
                self._pending.extend(self.decoder.flush_escape())
                break
            if not data:
                self._pending.extend(self.decoder.end())
                break
            self._pending.extend(self.decoder.feed(data))
            escape_pending = (
                not self._pending and bool(self.decoder._buffer) and self.decoder._buffer[0] == 0x1B
            )
        return self._pending.popleft() if self._pending else KeyEvent("unknown")

    async def write(self, value: str) -> None:
        self.stdout.write(value)
        self.stdout.flush()

    def flush(self) -> None:
        self.stdout.flush()

    @contextlib.contextmanager
    def raw_mode(self) -> Iterator[None]:
        if os.name == "nt" or not self.stdin.isatty():
            yield
            return
        import termios
        import tty

        descriptor = self.stdin.fileno()
        previous = termios.tcgetattr(descriptor)
        try:
            tty.setraw(descriptor)
            yield
        finally:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


class MemoryTerminal:
    """Deterministic host compatible with both interactive and stream tests."""

    def __init__(
        self,
        width: int = 80,
        height: int = 24,
        tty: bool = True,
        *,
        size: Size | None = None,
        inputs: tuple[bytes, ...] | None = None,
        capabilities: TerminalCapabilities | None = None,
    ) -> None:
        if size is not None:
            width, height = size.width, size.height
        self.width = width
        self.height = height
        self.tty = tty
        self.capabilities = capabilities or TerminalCapabilities()
        self._events: asyncio.Queue[TerminalEvent] = asyncio.Queue()
        self.output = OutputBuffer()
        self.writes = self.output
        self.flush_count = 0
        if size is not None or inputs is not None:
            decoder = KeyDecoder()
            for chunk in inputs or ():
                for event in decoder.feed(chunk):
                    self._events.put_nowait(event)
            for event in decoder.end():
                self._events.put_nowait(event)

    @property
    def is_tty(self) -> bool:
        return self.tty

    def size(self) -> tuple[int, int]:
        return self.width, self.height

    async def read_event(self) -> TerminalEvent:
        return await self._events.get()

    def write(self, value: str) -> Awaitable[None]:
        self.output.append(value)
        return _CompletedWrite()

    def flush(self) -> None:
        self.flush_count += 1

    @contextlib.contextmanager
    def raw_mode(self) -> Iterator[None]:
        yield

    def feed(self, *events: TerminalEvent) -> None:
        for event in events:
            self._events.put_nowait(event)

    def resize(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.feed(ResizeEvent(width, height))


_ESCAPE_SEQUENCES: tuple[tuple[bytes, KeyEvent], ...] = (
    (b"\x1b[A", KeyEvent("up")),
    (b"\x1b[B", KeyEvent("down")),
    (b"\x1b[C", KeyEvent("right")),
    (b"\x1b[D", KeyEvent("left")),
    (b"\x1b[H", KeyEvent("home")),
    (b"\x1b[F", KeyEvent("end")),
    (b"\x1bOH", KeyEvent("home")),
    (b"\x1bOF", KeyEvent("end")),
    (b"\x1b[3~", KeyEvent("delete")),
    (b"\x1b[5~", KeyEvent("page_up")),
    (b"\x1b[6~", KeyEvent("page_down")),
    (b"\x1b[1;2A", KeyEvent("up", shift=True)),
    (b"\x1b[1;2B", KeyEvent("down", shift=True)),
)


def _is_escape_prefix(buffer: bytearray) -> bool:
    value = bytes(buffer)
    sequences = [item[0] for item in _ESCAPE_SEQUENCES]
    sequences.extend((b"\x1b[200~", b"\x1b[201~"))
    return any(sequence.startswith(value) for sequence in sequences)


def _matching_escape(buffer: bytearray) -> tuple[int, KeyEvent] | None:
    value = bytes(buffer)
    for sequence, event in sorted(_ESCAPE_SEQUENCES, key=lambda item: len(item[0]), reverse=True):
        if value.startswith(sequence):
            return len(sequence), event
    return None


def _control_event(value: int) -> KeyEvent | None:
    mapping = {
        0x03: KeyEvent("ctrl_c", ctrl=True),
        0x04: KeyEvent("ctrl_d", ctrl=True),
        0x09: KeyEvent("tab"),
        0x0A: KeyEvent("enter"),
        0x0D: KeyEvent("enter"),
        0x0C: KeyEvent("ctrl_l", ctrl=True),
        0x12: KeyEvent("ctrl_r", ctrl=True),
        0x7F: KeyEvent("backspace"),
        0x08: KeyEvent("backspace"),
    }
    return mapping.get(value)


def _utf8_length(first: int) -> int:
    if first < 0x80:
        return 1
    if first & 0xE0 == 0xC0:
        return 2
    if first & 0xF0 == 0xE0:
        return 3
    if first & 0xF8 == 0xF0:
        return 4
    return 1


def detect_terminal_capabilities(output: TextIO | None = None) -> TerminalCapabilities:
    stream = output or sys.stdout
    term = os.environ.get("TERM", "")
    term_program = os.environ.get("TERM_PROGRAM", "")
    color_term = os.environ.get("COLORTERM", "").lower()
    is_tty = bool(stream.isatty())
    true_color = is_tty and color_term in {"truecolor", "24bit"}
    return TerminalCapabilities(
        color_depth=(1 << 24) if true_color else (256 if is_tty and term != "dumb" else 0),
        true_color=true_color,
        unicode=(getattr(stream, "encoding", None) or "").lower().startswith("utf"),
        mouse=is_tty and term != "dumb",
        bracketed_paste=is_tty and term != "dumb",
        alternate_screen=is_tty and term != "dumb",
        kitty_images="kitty" in term.casefold(),
        iterm_images=term_program.casefold() == "iterm.app",
    )


async def events(terminal: Terminal) -> AsyncIterator[TerminalEvent]:
    while True:
        event = await terminal.read_event()
        yield event
        if isinstance(event, EndOfInputEvent):
            return


__all__ = [
    "KeyDecoder",
    "MemoryTerminal",
    "OutputBuffer",
    "StdioTerminal",
    "Terminal",
    "TerminalCapabilities",
    "detect_terminal_capabilities",
    "events",
]
