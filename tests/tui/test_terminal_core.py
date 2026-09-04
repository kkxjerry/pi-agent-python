from __future__ import annotations

from pi_agent.tui import (
    AnsiRenderer,
    Cell,
    InputDecoder,
    KeyEvent,
    MemoryTerminal,
    PasteEvent,
    Size,
    Style,
    Surface,
    grapheme_width,
    text_width,
    truncate_text,
)


def test_width_handles_combining_wide_emoji_and_truncation() -> None:
    assert grapheme_width("e\u0301") == 1
    assert text_width("A你e\u0301") == 4
    assert text_width("👩\u200d💻") == 2
    assert truncate_text("A你BC", 4, ellipsis="…") == "A你…"


def test_input_decoder_handles_fragmented_keys_utf8_and_paste() -> None:
    decoder = InputDecoder()
    assert decoder.feed(b"\x1b[") == ()
    assert decoder.feed(b"A") == (KeyEvent("up"),)
    encoded = "你".encode()
    assert decoder.feed(encoded[:2]) == ()
    assert decoder.feed(encoded[2:]) == (KeyEvent("character", "你"),)
    assert decoder.feed(b"\x1b[200~one\n") == ()
    assert decoder.feed(b"two\x1b[201~") == (PasteEvent("one\ntwo"),)
    assert decoder.feed(b"\x01") == (KeyEvent("character", "a", ctrl=True),)


def test_surface_and_renderer_emit_only_changed_rows() -> None:
    output: list[str] = []
    renderer = AnsiRenderer(output.append)
    surface = Surface(8, 3)
    surface.draw_text(0, 0, "one")
    surface.set_cell(0, 1, Cell("你", Style(bold=True), 2))
    first = renderer.render(surface)
    assert first.full_redraw is True
    assert first.changed_rows == 3

    output.clear()
    second = renderer.render(surface)
    assert second.changed_rows == 0

    surface.draw_text(0, 2, "three")
    third = renderer.render(surface)
    assert third.changed_rows == 1
    assert "three" in "".join(output)


def test_memory_terminal_records_output() -> None:
    terminal = MemoryTerminal(size=Size(20, 5), inputs=(b"a", b"b"))
    terminal.write("hello")
    terminal.flush()
    assert terminal.output == ["hello"]
    assert terminal.flush_count == 1
