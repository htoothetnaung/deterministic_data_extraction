"""Pillow implementation for non-OCR image inspection."""
from __future__ import annotations

import time
from pathlib import Path

from app.models.parser_benchmark import ParserRunResult
from app.services.parsers.base import bbox_from_values, input_type_for, make_block, module_available, ok_result, skipped_result, structured_preview_from_blocks

LIBRARY_ID = "pillow"
DISPLAY_NAME = "Pillow image metadata"
SUPPORTED_INPUT_TYPES = ["image"]


def is_available() -> bool:
    return module_available("PIL")


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    if input_type_for(input_path) != "image":
        return skipped_result(LIBRARY_ID, input_path, "Pillow metadata parser only supports image inputs.", preview_chars=preview_chars)
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, "Install pillow to enable this parser.", preview_chars=preview_chars)

    from PIL import Image

    with Image.open(input_path) as image:
        metadata = {
            "format": image.format,
            "mode": image.mode,
            "width": image.width,
            "height": image.height,
            "frames": getattr(image, "n_frames", 1),
            "dpi": image.info.get("dpi"),
        }

    text = (
        f"Image metadata for {input_path.name}\n"
        f"Format: {metadata['format']}\n"
        f"Mode: {metadata['mode']}\n"
        f"Size: {metadata['width']} x {metadata['height']}\n"
        f"Frames: {metadata['frames']}\n"
        f"DPI: {metadata['dpi']}"
    )
    block = make_block(
        LIBRARY_ID,
        1,
        "image",
        text,
        bbox=bbox_from_values(0, 0, metadata["width"], metadata["height"]),
        provenance={"source": "PIL.Image.open", "metadata": metadata},
    )
    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=1,
        images=1,
        structured_preview=structured_preview_from_blocks([block] if block else [], text, preview_chars, metadata),
        preview_chars=preview_chars,
    )
