from __future__ import annotations

import base64
import struct
from pathlib import Path

import pytest

from pi_agent.coding_agent.images import (
    ImageLimits,
    ImageProcessingError,
    ImageProcessor,
    image_dimensions,
)


def gif(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 16


def test_image_processor_builds_model_content_without_optional_dependencies() -> None:
    data = gif(2, 3)
    processed = ImageProcessor().process_bytes(data, mime_type="image/gif")
    assert processed.original.width == 2
    assert processed.final.height == 3
    assert processed.resized is False
    assert base64.b64decode(processed.content.data) == data
    assert image_dimensions(data, "image/gif").pixels == 6


def test_image_processor_enforces_input_and_dimension_limits(tmp_path: Path) -> None:
    with pytest.raises(ImageProcessingError, match="input bytes"):
        ImageProcessor(ImageLimits(max_input_bytes=5)).process_bytes(
            gif(2, 2),
            mime_type="image/gif",
        )

    path = tmp_path / "invalid.txt"
    path.write_bytes(b"not-an-image")
    with pytest.raises(ImageProcessingError, match="extension"):
        ImageProcessor().process_path(path)
