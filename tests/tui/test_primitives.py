from __future__ import annotations

from pi_agent.tui import Cell, Rect, Size, Style, Surface


def test_rect_intersection_and_surface_crop() -> None:
    assert Rect(0, 0, 5, 5).intersect(Rect(3, 2, 5, 5)) == Rect(3, 2, 2, 3)
    surface = Surface(6, 3)
    surface.draw_text(0, 0, "abcdef")
    surface.draw_text(0, 1, "你ab", style=Style(bold=True))
    cropped = surface.crop(Rect(1, 0, 4, 2))
    assert cropped.size == Size(4, 2)
    assert cropped.plain_lines()[0].startswith("bcde")


def test_wide_cell_replacement_clears_continuation() -> None:
    surface = Surface(4, 1)
    surface.set_cell(0, 0, Cell("你", Style(), 2))
    assert surface.cell(1, 0).width == 0
    surface.set_cell(1, 0, Cell("x", Style(), 1))
    assert surface.cell(0, 0).text == " "
    assert surface.cell(1, 0).text == "x"
