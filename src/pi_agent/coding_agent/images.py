from __future__ import annotations

import base64
import binascii
import importlib
import io
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias, cast

from pi_agent.ai import ImageContent

ImageMimeType: TypeAlias = Literal["image/png", "image/jpeg", "image/gif", "image/webp"]


class ImageProcessingError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class ImageDimensions:
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("image dimensions must be positive")

    @property
    def pixels(self) -> int:
        return self.width * self.height


@dataclass(slots=True, frozen=True)
class ImageLimits:
    max_input_bytes: int = 20 * 1024 * 1024
    max_output_bytes: int = 10 * 1024 * 1024
    max_width: int = 4096
    max_height: int = 4096
    max_pixels: int = 16_000_000

    def __post_init__(self) -> None:
        if (
            min(
                self.max_input_bytes,
                self.max_output_bytes,
                self.max_width,
                self.max_height,
                self.max_pixels,
            )
            <= 0
        ):
            raise ValueError("image limits must be positive")


@dataclass(slots=True, frozen=True)
class ProcessedImage:
    content: ImageContent
    original: ImageDimensions
    final: ImageDimensions
    input_bytes: int
    output_bytes: int
    resized: bool


class ImageBackend(Protocol):
    def resize(
        self,
        data: bytes,
        mime_type: ImageMimeType,
        dimensions: ImageDimensions,
    ) -> tuple[bytes, ImageMimeType]: ...


class PillowImageBackend:
    """Optional high-quality resize backend imported only when used."""

    def resize(
        self,
        data: bytes,
        mime_type: ImageMimeType,
        dimensions: ImageDimensions,
    ) -> tuple[bytes, ImageMimeType]:
        try:
            image_module = cast(Any, importlib.import_module("PIL.Image"))
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImageProcessingError(
                "image resize requires the optional Pillow dependency; "
                "install pi-agent-python[image]"
            ) from exc
        try:
            with image_module.open(io.BytesIO(data)) as image:
                converted = image.convert("RGBA" if image.mode in {"RGBA", "LA", "P"} else "RGB")
                resized = converted.resize(
                    (dimensions.width, dimensions.height),
                    resample=image_module.Resampling.LANCZOS,
                )
                output = io.BytesIO()
                output_format = "PNG" if "A" in resized.mode else "JPEG"
                output_mime: ImageMimeType = "image/png" if output_format == "PNG" else "image/jpeg"
                save_options: dict[str, object] = {"optimize": True}
                if output_format == "JPEG":
                    save_options["quality"] = 88
                resized.save(output, format=output_format, **save_options)
                return output.getvalue(), output_mime
        except ImageProcessingError:
            raise
        except Exception as exc:
            raise ImageProcessingError(f"could not resize image: {exc}") from exc


class ImageProcessor:
    def __init__(
        self,
        limits: ImageLimits | None = None,
        *,
        backend: ImageBackend | None = None,
    ) -> None:
        self.limits = limits or ImageLimits()
        self.backend = backend

    def process_path(self, path: str | Path) -> ProcessedImage:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise ImageProcessingError(f"image path is not a file: {source}")
        try:
            data = source.read_bytes()
        except OSError as exc:
            raise ImageProcessingError(f"could not read image {source}: {exc}") from exc
        return self.process_bytes(data, mime_type=_mime_from_extension(source.suffix))

    def process_content(self, content: ImageContent) -> ProcessedImage:
        try:
            data = base64.b64decode(content.data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ImageProcessingError("image content is not valid base64") from exc
        return self.process_bytes(data, mime_type=content.mime_type)

    def process_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
    ) -> ProcessedImage:
        if not data:
            raise ImageProcessingError("image payload is empty")
        if len(data) > self.limits.max_input_bytes:
            raise ImageProcessingError(
                f"image payload exceeds {self.limits.max_input_bytes} input bytes"
            )
        normalized = normalize_mime_type(mime_type or detect_mime_type(data))
        original = image_dimensions(data, normalized)
        target = bounded_dimensions(original, self.limits)
        output = data
        output_mime = normalized
        resized = target != original
        if resized:
            backend = self.backend or PillowImageBackend()
            output, output_mime = backend.resize(data, normalized, target)
            actual = image_dimensions(output, output_mime)
            if actual != target:
                raise ImageProcessingError(
                    f"image backend returned {actual.width}x{actual.height}; "
                    f"expected {target.width}x{target.height}"
                )
        final = image_dimensions(output, output_mime)
        if final.width > self.limits.max_width or final.height > self.limits.max_height:
            raise ImageProcessingError("processed image dimensions still exceed configured limits")
        if final.pixels > self.limits.max_pixels:
            raise ImageProcessingError(
                "processed image pixel count still exceeds configured limits"
            )
        if len(output) > self.limits.max_output_bytes:
            raise ImageProcessingError(
                f"processed image exceeds {self.limits.max_output_bytes} output bytes"
            )
        return ProcessedImage(
            ImageContent(base64.b64encode(output).decode("ascii"), output_mime),
            original,
            final,
            len(data),
            len(output),
            resized,
        )


def normalize_mime_type(value: str) -> ImageMimeType:
    normalized = value.lower().strip().split(";", 1)[0]
    aliases = {
        "image/jpg": "image/jpeg",
        "image/x-png": "image/png",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        raise ImageProcessingError(f"unsupported image MIME type: {value}")
    return cast(ImageMimeType, normalized)


def detect_mime_type(data: bytes) -> ImageMimeType:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ImageProcessingError("could not detect image format")


def image_dimensions(data: bytes, mime_type: str | None = None) -> ImageDimensions:
    normalized = normalize_mime_type(mime_type or detect_mime_type(data))
    if normalized == "image/png":
        if len(data) < 24 or data[12:16] != b"IHDR":
            raise ImageProcessingError("invalid PNG header")
        width, height = struct.unpack(">II", data[16:24])
    elif normalized == "image/gif":
        if len(data) < 10 or data[:6] not in {b"GIF87a", b"GIF89a"}:
            raise ImageProcessingError("invalid GIF header")
        width, height = struct.unpack("<HH", data[6:10])
    elif normalized == "image/jpeg":
        width, height = _jpeg_dimensions(data)
    else:
        width, height = _webp_dimensions(data)
    return ImageDimensions(width, height)


def bounded_dimensions(
    dimensions: ImageDimensions,
    limits: ImageLimits,
) -> ImageDimensions:
    scale = min(
        1.0,
        limits.max_width / dimensions.width,
        limits.max_height / dimensions.height,
        (limits.max_pixels / dimensions.pixels) ** 0.5,
    )
    return ImageDimensions(
        max(1, int(dimensions.width * scale)),
        max(1, int(dimensions.height * scale)),
    )


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        raise ImageProcessingError("invalid JPEG header")
    position = 2
    frame_markers = {
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
    while position + 4 <= len(data):
        while position < len(data) and data[position] != 0xFF:
            position += 1
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker in {0xD8, 0xD9}:
            continue
        if position + 2 > len(data):
            break
        length = int.from_bytes(data[position : position + 2], "big")
        if length < 2 or position + length > len(data):
            raise ImageProcessingError("invalid JPEG segment length")
        if marker in frame_markers:
            if length < 7:
                raise ImageProcessingError("invalid JPEG frame header")
            height = int.from_bytes(data[position + 3 : position + 5], "big")
            width = int.from_bytes(data[position + 5 : position + 7], "big")
            return width, height
        position += length
    raise ImageProcessingError("JPEG dimensions were not found")


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 25 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ImageProcessingError("invalid WebP header")
    kind = data[12:16]
    if kind == b"VP8X" and len(data) >= 30:
        return (
            1 + int.from_bytes(data[24:27], "little"),
            1 + int.from_bytes(data[27:30], "little"),
        )
    if kind == b"VP8 ":
        marker = data.find(b"\x9d\x01\x2a", 20)
        if marker < 0 or marker + 7 > len(data):
            raise ImageProcessingError("invalid lossy WebP frame")
        return (
            int.from_bytes(data[marker + 3 : marker + 5], "little") & 0x3FFF,
            int.from_bytes(data[marker + 5 : marker + 7], "little") & 0x3FFF,
        )
    if kind == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    raise ImageProcessingError(f"unsupported WebP chunk: {kind!r}")


def _mime_from_extension(extension: str) -> ImageMimeType:
    normalized = extension.lower()
    mapping: dict[str, ImageMimeType] = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    try:
        return mapping[normalized]
    except KeyError as exc:
        raise ImageProcessingError(f"unsupported image extension: {extension}") from exc


__all__ = [
    "ImageBackend",
    "ImageDimensions",
    "ImageLimits",
    "ImageMimeType",
    "ImageProcessingError",
    "ImageProcessor",
    "PillowImageBackend",
    "ProcessedImage",
    "bounded_dimensions",
    "detect_mime_type",
    "image_dimensions",
    "normalize_mime_type",
]
