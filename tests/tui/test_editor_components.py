from __future__ import annotations

from pi_agent.tui import KeyEvent, PasteEvent, Rect, Size, Surface, TextEditor
from pi_agent.tui.widgets import SelectList, SelectOption, TranscriptItem, TranscriptView


def test_editor_multiline_navigation_submit_and_history() -> None:
    editor = TextEditor()
    editor.handle(PasteEvent("one\ntwo"))
    assert editor.text == "one\ntwo"
    editor.handle(KeyEvent("home"))
    editor.handle(KeyEvent("character", "X"))
    assert editor.text.endswith("Xtwo")
    assert editor.handle(KeyEvent("enter", shift=True)).kind == "changed"
    editor.handle(KeyEvent("character", "three"))
    submitted = editor.submit()
    assert "three" in submitted
    editor.remember(submitted)
    editor.handle(KeyEvent("up"))
    assert submitted in editor.text


def test_editor_render_is_clipped_to_requested_viewport() -> None:
    editor = TextEditor("A你BCDE")
    rendered = editor.render(4, 2, prompt="> ")
    assert len(rendered.lines) <= 2
    assert 0 <= rendered.cursor_x < 4
    assert 0 <= rendered.cursor_y < 2


def test_transcript_and_selector_render_to_surface() -> None:
    surface = Surface(40, 10)
    transcript = TranscriptView(
        [
            TranscriptItem("user", "inspect the repository"),
            TranscriptItem("assistant", "done"),
        ]
    )
    transcript.render(surface, Rect(0, 0, 40, 6))
    assert "inspect the repository" in "\n".join(surface.plain_lines())

    selector = SelectList(
        [
            SelectOption("one", "One", "first"),
            SelectOption("two", "Two", "second"),
        ],
        title="Choose",
    )
    selector.handle(KeyEvent("down"))
    selected = selector.handle(KeyEvent("enter"))
    assert isinstance(selected, SelectOption)
    assert selected.id == "two"
    selector.render(surface, Rect(0, 0, Size(40, 10).width, 6))
