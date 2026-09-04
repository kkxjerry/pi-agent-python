from __future__ import annotations

from itertools import product

from pi_agent.tui import InputDecoder, Style, Surface, grapheme_width, graphemes, text_width


def test_grapheme_width_is_additive_for_representative_clusters() -> None:
    samples = ("A", "你", "e\u0301", "👩\u200d💻", "🇺🇸", "🙂")
    for count in range(5):
        for sample in samples:
            value = sample * count
            assert text_width(value) == sum(grapheme_width(cluster) for cluster in graphemes(value))


def test_surface_diff_converges_after_clone() -> None:
    for width, height in product((1, 2, 8, 20), (1, 2, 5)):
        surface = Surface(width, height)
        for row in range(height):
            surface.draw_text(0, row, f"row-{row}-你", style=Style(bold=bool(row % 2)))
        clone = surface.clone()
        assert surface.diff(clone) == ()


def test_input_fragmentation_does_not_change_decoded_events() -> None:
    payload = b"a\x1b[A\x1b[200~hello\nworld\x1b[201~" + "你".encode()
    baseline_decoder = InputDecoder()
    baseline = (*baseline_decoder.feed(payload), *baseline_decoder.flush_escape())
    for chunk_size in range(1, 8):
        decoder = InputDecoder()
        events = []
        for index in range(0, len(payload), chunk_size):
            events.extend(decoder.feed(payload[index : index + chunk_size]))
        events.extend(decoder.flush_escape())
        assert tuple(events) == baseline
