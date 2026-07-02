"""PyMuPDF implementation for PDF text extraction."""
from __future__ import annotations

import time
from pathlib import Path

from app.models.parser_benchmark import ParserRunResult
from app.services.parsers.base import (
    bbox_from_values,
    blocks_to_markdown,
    column_aware_blocks,
    input_type_for,
    make_block,
    module_available,
    ok_result,
    skipped_result,
    structured_preview_from_blocks,
)

LIBRARY_ID = "pymupdf"
DISPLAY_NAME = "PyMuPDF"
SUPPORTED_INPUT_TYPES = ["pdf"]


def is_available() -> bool:
    return module_available("fitz")


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    if input_type_for(input_path) != "pdf":
        return skipped_result(LIBRARY_ID, input_path, "PyMuPDF only supports PDF inputs.", preview_chars=preview_chars)
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, "Install pymupdf to enable this parser.", preview_chars=preview_chars)

    import fitz

    text_parts: list[str] = []
    blocks: list[dict[str, object]] = []
    image_count = 0
    with fitz.open(str(input_path)) as doc:
        for page_index, page in enumerate(doc, start=1):
            text_parts.append(page.get_text("text", sort=False) or "")
            for x0, y0, x1, y1, text, block_no, block_type in page.get_text("blocks", sort=False):
                block_kind = "image" if int(block_type) == 1 else "text"
                block = make_block(
                    LIBRARY_ID,
                    page_index,
                    block_kind,
                    text,
                    bbox=bbox_from_values(x0, y0, x1, y1),
                    provenance={
                        "source": "pymupdf.page.get_text(blocks)",
                        "block_no": block_no,
                        "block_type": block_type,
                    },
                )
                if block:
                    blocks.append(block)
            image_count += len(page.get_images(full=True))
        pages = doc.page_count

    naive_text = "\n\n".join(part for part in text_parts if part.strip())
    ordered_blocks = column_aware_blocks(blocks)
    text = blocks_to_markdown(ordered_blocks) or naive_text

    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=pages,
        images=image_count,
        structured_preview=structured_preview_from_blocks(
            blocks,
            naive_text,
            preview_chars,
            {
                "image_objects": image_count,
                "native_text_mode": "page.get_text('text', sort=False)",
                "structured_text_mode": "page.get_text('blocks', sort=False)",
            },
        ),
        preview_chars=preview_chars,
    )
