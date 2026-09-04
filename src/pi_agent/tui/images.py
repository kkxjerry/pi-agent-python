from __future__ import annotations

import base64
from dataclasses import dataclass

from pi_agent.ai import ImageContent
from pi_agent.coding_agent.images import ProcessedImage

from .terminal import TerminalCapabilities


@dataclass(slots=True, frozen=True)
class TerminalImage:
    content: ImageContent
    width: int
    height: int
    byte_length: int
    columns: int
    rows: int

    @classmethod
    def from_processed(
        cls,
        image: ProcessedImage,
        *,
        max_columns: int,
        max_rows: int,
        cell_width_px: int = 8,
        cell_height_px: int = 16,
    ) -> TerminalImage:
        columns, rows = fit_terminal_cells(
            image.final.width,
            image.final.height,
            max_columns=max_columns,
            max_rows=max_rows,
            cell_width_px=cell_width_px,
            cell_height_px=cell_height_px,
        )
        return cls(
            content=image.content,
            width=image.final.width,
            height=image.final.height,
            byte_length=image.output_bytes,
            columns=columns,
            rows=rows,
        )


def fit_terminal_cells(
    width: int,
    height: int,
    *,
    max_columns: int,
    max_rows: int,
    cell_width_px: int = 8,
    cell_height_px: int = 16,
) -> tuple[int, int]:
    if min(width, height, max_columns, max_rows, cell_width_px, cell_height_px) <= 0:
        raise ValueError("image and terminal dimensions must be positive")
    natural_columns = max(1, round(width / cell_width_px))
    natural_rows = max(1, round(height / cell_height_px))
    scale = min(1.0, max_columns / natural_columns, max_rows / natural_rows)
    return (
        max(1, round(natural_columns * scale)),
        max(1, round(natural_rows * scale)),
    )


def render_terminal_image(
    image: TerminalImage,
    capabilities: TerminalCapabilities,
    *,
    image_id: int = 1,
) -> str:
    if capabilities.kitty_images and image.content.mime_type == "image/png":
        return encode_kitty_image(image, image_id=image_id)
    if capabilities.iterm_images:
        return encode_iterm_image(image)
    return (
        f"[image {image.content.mime_type} {image.width}x{image.height}, {image.byte_length} bytes]"
    )


def encode_kitty_image(image: TerminalImage, *, image_id: int = 1) -> str:
    encoded = image.content.data
    chunks = [encoded[index : index + 4096] for index in range(0, len(encoded), 4096)] or [""]
    result: list[str] = []
    format_code = 100 if image.content.mime_type == "image/png" else 0
    for index, chunk in enumerate(chunks):
        more = 1 if index < len(chunks) - 1 else 0
        prefix = (
            f"a=T,t=d,f={format_code},i={image_id},c={image.columns},r={image.rows},m={more}"
            if index == 0
            else f"m={more}"
        )
        result.append(f"\x1b_G{prefix};{chunk}\x1b\\")
    return "".join(result)


def encode_iterm_image(image: TerminalImage) -> str:
    parameters = f"inline=1;width={image.columns};height={image.rows};preserveAspectRatio=1"
    return f"\x1b]1337;File={parameters}:{image.content.data}\x07"


def terminal_image_from_bytes(
    data: bytes,
    mime_type: str,
    *,
    width: int,
    height: int,
    max_columns: int,
    max_rows: int,
) -> TerminalImage:
    columns, rows = fit_terminal_cells(
        width,
        height,
        max_columns=max_columns,
        max_rows=max_rows,
    )
    return TerminalImage(
        ImageContent(base64.b64encode(data).decode("ascii"), mime_type),
        width,
        height,
        len(data),
        columns,
        rows,
    )


__all__ = [
    "TerminalImage",
    "encode_iterm_image",
    "encode_kitty_image",
    "fit_terminal_cells",
    "render_terminal_image",
    "terminal_image_from_bytes",
]
