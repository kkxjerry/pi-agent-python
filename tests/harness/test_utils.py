from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.harness import (
    LocalExecutionEnv,
    detect_supported_image_mime_type,
    normalize_tool_path,
    resolve_read_tool_path,
    truncate_head,
    truncate_tail,
)
from pi_agent.harness.result import get_or_raise


def test_truncate_head_never_returns_partial_first_line() -> None:
    result = truncate_head("x" * 20, max_lines=10, max_bytes=10)
    assert result.content == ""
    assert result.truncated is True
    assert result.truncated_by == "bytes"
    assert result.first_line_exceeds_limit is True


def test_truncate_tail_keeps_valid_utf8_suffix_and_tracks_partial_line() -> None:
    result = truncate_tail("prefix-😀😀😀", max_lines=10, max_bytes=8)
    assert result.content == "😀😀"
    assert result.output_bytes == 8
    assert result.last_line_partial is True
    assert result.truncated_by == "bytes"


def test_image_detection_rejects_animated_png_and_jpeg_xl() -> None:
    png = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
    assert detect_supported_image_mime_type(png) == "image/png"

    animated = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + b"0" * 17
        + (8).to_bytes(4, "big")
        + b"acTL"
    )
    assert detect_supported_image_mime_type(animated) is None
    assert detect_supported_image_mime_type(b"\xff\xd8\xff\xf7") is None


@pytest.mark.asyncio
async def test_read_path_variants_handle_at_prefix_unicode_spaces_and_nfd(
    tmp_path: Path,
) -> None:
    env = LocalExecutionEnv(str(tmp_path))
    decomposed = "cafe\u0301.txt"
    (tmp_path / decomposed).write_text("ok", encoding="utf-8")

    assert normalize_tool_path("@folder\u00a0name/file") == "folder name/file"
    resolved = await resolve_read_tool_path(env, "café.txt")

    # APFS may consider NFC and NFD paths equivalent and therefore accept the
    # first candidate. The returned path must still address the requested file.
    assert get_or_raise(await env.read_text_file(resolved)) == "ok"
