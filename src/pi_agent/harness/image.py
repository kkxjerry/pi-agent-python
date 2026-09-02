from __future__ import annotations

import base64

_PNG_SIGNATURE = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))


def detect_supported_image_mime_type(buffer: bytes) -> str | None:
    if buffer.startswith(bytes((0xFF, 0xD8, 0xFF))):
        return None if len(buffer) > 3 and buffer[3] == 0xF7 else "image/jpeg"
    if buffer.startswith(_PNG_SIGNATURE):
        return "image/png" if _is_png(buffer) and not _is_animated_png(buffer) else None
    if buffer.startswith(b"GIF"):
        return "image/gif"
    if buffer.startswith(b"RIFF") and buffer[8:12] == b"WEBP":
        return "image/webp"
    if buffer.startswith(b"BM") and _is_bmp(buffer):
        return "image/bmp"
    return None


def encode_base64(buffer: bytes) -> str:
    return base64.b64encode(buffer).decode("ascii")


def _is_png(buffer: bytes) -> bool:
    return (
        len(buffer) >= 16 and int.from_bytes(buffer[8:12], "big") == 13 and buffer[12:16] == b"IHDR"
    )


def _is_animated_png(buffer: bytes) -> bool:
    offset = len(_PNG_SIGNATURE)
    while offset + 8 <= len(buffer):
        chunk_length = int.from_bytes(buffer[offset : offset + 4], "big")
        chunk_type = buffer[offset + 4 : offset + 8]
        if chunk_type == b"acTL":
            return True
        if chunk_type == b"IDAT":
            return False
        next_offset = offset + 8 + chunk_length + 4
        if next_offset <= offset or next_offset > len(buffer):
            return False
        offset = next_offset
    return False


def _is_bmp(buffer: bytes) -> bool:
    if len(buffer) < 26:
        return False
    declared_size = int.from_bytes(buffer[2:6], "little")
    pixel_offset = int.from_bytes(buffer[10:14], "little")
    dib_size = int.from_bytes(buffer[14:18], "little")
    if declared_size and declared_size < 26:
        return False
    if pixel_offset < 14 + dib_size:
        return False
    if declared_size and pixel_offset >= declared_size:
        return False
    if dib_size == 12:
        planes = int.from_bytes(buffer[22:24], "little")
        bits = int.from_bytes(buffer[24:26], "little")
    elif 40 <= dib_size <= 124 and len(buffer) >= 30:
        planes = int.from_bytes(buffer[26:28], "little")
        bits = int.from_bytes(buffer[28:30], "little")
    else:
        return False
    return planes == 1 and bits in {1, 4, 8, 16, 24, 32}
