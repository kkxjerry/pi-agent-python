from __future__ import annotations

from pi_agent.tui.images import (
    TerminalImage,
    encode_iterm_image,
    encode_kitty_image,
    fit_terminal_cells,
    terminal_image_from_bytes,
)


def test_terminal_image_fits_cells_and_encodes_supported_protocols() -> None:
    columns, rows = fit_terminal_cells(
        800,
        400,
        max_columns=40,
        max_rows=10,
    )
    assert columns <= 40
    assert rows <= 10

    image = terminal_image_from_bytes(
        b"png-data",
        "image/png",
        width=800,
        height=400,
        max_columns=40,
        max_rows=10,
    )
    assert isinstance(image, TerminalImage)
    assert encode_kitty_image(image).startswith("\x1b_G")
    assert encode_iterm_image(image).startswith("\x1b]1337;File=")
