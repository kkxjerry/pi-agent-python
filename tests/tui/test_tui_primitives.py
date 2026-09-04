from __future__ import annotations

from pi_agent.tui import (
    AnsiRenderer,
    Canvas,
    KeyDecoder,
    KeyEvent,
    PasteEvent,
    TextEditor,
    cell_width,
    strip_ansi,
    truncate_cells,
    wrap_cells,
)


def test_cell_width_wrap_and_truncate_handle_wide_and_combining_text() -> None:
    assert cell_width("abc") == 3
    assert cell_width("中文") == 4
    assert cell_width("e\u0301") == 1
    assert wrap_cells("ab中文cd", 4) == ("ab中", "文cd")
    assert cell_width(truncate_cells("abcdef", 4)) <= 4


def test_canvas_and_renderer_only_update_changed_lines() -> None:
    canvas = Canvas(12, 3)
    canvas.draw_text(0, 0, "hello")
    first = canvas.frame(cursor_x=1, cursor_y=2, title="demo")
    renderer = AnsiRenderer()
    entered = renderer.enter()
    initial = renderer.render(first)
    assert "\x1b[?1049h" in entered
    assert "hello" in strip_ansi(initial)

    canvas.draw_text(0, 1, "changed")
    update = renderer.render(canvas.frame(cursor_x=2, cursor_y=2, title="demo"))
    assert "\x1b[2;1H" in update
    assert "\x1b[1;1H" not in update
    assert "changed" in strip_ansi(update)


def test_key_decoder_handles_utf8_navigation_and_bracketed_paste() -> None:
    decoder = KeyDecoder()
    assert decoder.feed("中".encode()) == (KeyEvent("character", "中"),)
    assert decoder.feed(b"\x1b[A") == (KeyEvent("up"),)
    assert decoder.feed(b"\x1b[200~line 1\nline 2\x1b[201~") == (PasteEvent("line 1\nline 2"),)
    assert decoder.feed(b"\x03") == (KeyEvent("ctrl_c", ctrl=True),)


def test_text_editor_edits_multiline_history_and_render_cursor() -> None:
    editor = TextEditor()
    for character in "hello":
        assert editor.handle(KeyEvent("character", character)) == "changed"
    editor.handle(KeyEvent("enter", shift=True))
    editor.handle(KeyEvent("character", "世"))
    editor.handle(KeyEvent("character", "界"))
    assert editor.text == "hello\n世界"
    rendered = editor.render(10)
    assert rendered.cursor_y == 1
    assert rendered.cursor_x == 6

    value = editor.submit()
    assert value == "hello\n世界"
    editor.previous_history()
    assert editor.text == value
    editor.handle(KeyEvent("up"))
    editor.handle(KeyEvent("home"))
    editor.handle(KeyEvent("delete"))
    assert "h" not in editor.text.splitlines()[0][:1]
