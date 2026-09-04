from __future__ import annotations

import base64
from pathlib import Path

import pytest

from pi_agent.coding_agent.attachments import AttachmentError, AttachmentResolver
from pi_agent.tui import (
    CommandCompleter,
    CompletionItem,
    CompletionRequest,
    FileCompleter,
    apply_completion,
)

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nksAAAAASUVORK5CYII="
)


def test_command_and_file_completion_are_deterministic(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("a", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    commands = CommandCompleter((("model", "choose model"), ("compact", "compact")))
    command_items = commands.complete(CompletionRequest("/mo", 3, tmp_path))
    assert [item.value for item in command_items] == ["/model"]

    files = FileCompleter().complete(CompletionRequest("attach @a", 9, tmp_path))
    assert {item.value for item in files} == {"@assets/", "@alpha.txt"}
    text, cursor = apply_completion("attach @a", 9, CompletionItem("@alpha.txt"))
    assert text == "attach @alpha.txt"
    assert cursor == len(text)


def test_attachment_resolver_extracts_image_and_enforces_roots(tmp_path: Path) -> None:
    image = tmp_path / "image.png"
    image.write_bytes(_ONE_PIXEL_PNG)
    resolver = AttachmentResolver(tmp_path)

    resolved = resolver.resolve_prompt("analyze @image:image.png carefully")

    assert resolved.text == "analyze carefully"
    assert len(resolved.images) == 1
    assert resolved.attachments[0].width == 1
    assert resolved.attachments[0].height == 1
    assert resolved.images[0].mime_type == "image/png"

    outside = tmp_path.parent / f"{tmp_path.name}-outside.png"
    outside.write_bytes(_ONE_PIXEL_PNG)
    with pytest.raises(AttachmentError, match="outside allowed roots"):
        resolver.resolve_file(outside)


def test_attachment_resolver_rejects_large_or_binary_files(tmp_path: Path) -> None:
    large = tmp_path / "large.txt"
    large.write_text("x" * 100, encoding="utf-8")
    resolver = AttachmentResolver(tmp_path, max_text_bytes=10, include_text=True)
    with pytest.raises(AttachmentError, match="exceeds"):
        resolver.resolve_file(large)

    binary = tmp_path / "data.bin"
    binary.write_bytes(b"\xff\xfe\xfd")
    with pytest.raises(AttachmentError, match="unsupported binary"):
        resolver.resolve_file(binary)
