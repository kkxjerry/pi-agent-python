from __future__ import annotations

import base64
import shlex
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pi_agent.ai import ImageContent
from pi_agent.harness import detect_supported_image_mime_type

AttachmentKind = Literal["image", "text"]


class AttachmentError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class Attachment:
    path: Path
    kind: AttachmentKind
    size: int
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    text: str | None = None
    image: ImageContent | None = None


@dataclass(slots=True, frozen=True)
class ResolvedPrompt:
    text: str
    attachments: tuple[Attachment, ...]

    @property
    def images(self) -> tuple[ImageContent, ...]:
        return tuple(item.image for item in self.attachments if item.image is not None)


class AttachmentResolver:
    """Resolve explicit @file references without reading outside configured roots."""

    def __init__(
        self,
        cwd: str | Path,
        *,
        allowed_roots: tuple[str | Path, ...] = (),
        max_image_bytes: int = 20 * 1024 * 1024,
        max_text_bytes: int = 1 * 1024 * 1024,
        max_image_pixels: int = 80_000_000,
        include_text: bool = False,
    ) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        roots = allowed_roots or (self.cwd,)
        self.allowed_roots = tuple(Path(root).expanduser().resolve() for root in roots)
        self.max_image_bytes = max_image_bytes
        self.max_text_bytes = max_text_bytes
        self.max_image_pixels = max_image_pixels
        self.include_text = include_text
        if min(max_image_bytes, max_text_bytes, max_image_pixels) <= 0:
            raise ValueError("attachment limits must be positive")

    def resolve_prompt(self, prompt: str) -> ResolvedPrompt:
        tokens = _tokens(prompt)
        attachments: list[Attachment] = []
        retained: list[str] = []
        for token in tokens:
            path_token = _attachment_token(token)
            if path_token is None:
                retained.append(token)
                continue
            path = self._resolve_path(path_token)
            attachment = self.resolve_file(path)
            if attachment.kind == "text" and not self.include_text:
                retained.append(token)
                continue
            attachments.append(attachment)
        return ResolvedPrompt(" ".join(retained).strip(), tuple(attachments))

    def resolve_file(self, path: str | Path) -> Attachment:
        actual = self._resolve_path(str(path))
        if not actual.is_file():
            raise AttachmentError(f"attachment is not a regular file: {actual}")
        try:
            size = actual.stat().st_size
            data = actual.read_bytes()
        except OSError as exc:
            raise AttachmentError(f"could not read attachment {actual}: {exc}") from exc
        mime_type = detect_supported_image_mime_type(data)
        if mime_type is not None:
            if size > self.max_image_bytes:
                raise AttachmentError(
                    f"image attachment exceeds {self.max_image_bytes} bytes: {actual}"
                )
            width, height = image_dimensions(data, mime_type)
            if width is not None and height is not None and width * height > self.max_image_pixels:
                raise AttachmentError(
                    f"image attachment exceeds {self.max_image_pixels} pixels: {width}x{height}"
                )
            return Attachment(
                path=actual,
                kind="image",
                size=size,
                mime_type=mime_type,
                width=width,
                height=height,
                image=ImageContent(base64.b64encode(data).decode("ascii"), mime_type),
            )
        if size > self.max_text_bytes:
            raise AttachmentError(f"text attachment exceeds {self.max_text_bytes} bytes: {actual}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AttachmentError(f"unsupported binary attachment type for {actual}") from exc
        return Attachment(path=actual, kind="text", size=size, text=text)

    def _resolve_path(self, value: str) -> Path:
        raw = value[1:] if value.startswith("@") else value
        if raw.startswith("image:"):
            raw = raw[6:]
        path = Path(raw).expanduser()
        resolved = path.resolve() if path.is_absolute() else (self.cwd / path).resolve()
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise AttachmentError(f"attachment path is outside allowed roots: {resolved}")


def image_dimensions(data: bytes, mime_type: str) -> tuple[int | None, int | None]:
    if mime_type == "image/png" and len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    if mime_type == "image/gif" and len(data) >= 10 and data[:6] in {b"GIF87a", b"GIF89a"}:
        width, height = struct.unpack("<HH", data[6:10])
        return width, height
    if mime_type == "image/webp" and len(data) >= 30:
        return _webp_dimensions(data)
    if mime_type == "image/jpeg":
        return _jpeg_dimensions(data)
    return None, None


def _jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if not data.startswith(b"\xff\xd8"):
        return None, None
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            break
        length = int.from_bytes(data[index : index + 2], "big")
        if length < 2 or index + length > len(data):
            break
        if (
            marker
            in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }
            and length >= 7
        ):
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += length
    return None, None


def _webp_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None, None
    kind = data[12:16]
    if kind == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if kind == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None, None


def _tokens(prompt: str) -> list[str]:
    try:
        return shlex.split(prompt, posix=True)
    except ValueError:
        return prompt.split()


def _attachment_token(token: str) -> str | None:
    return token if token.startswith("@") and len(token) > 1 else None
