"""pypdf implementation for native PDF text extraction."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

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

LIBRARY_ID = "pypdf"
DISPLAY_NAME = "pypdf"
SUPPORTED_INPUT_TYPES = ["pdf"]


def is_available() -> bool:
    return module_available("pypdf")


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    if input_type_for(input_path) != "pdf":
        return skipped_result(LIBRARY_ID, input_path, "pypdf only supports PDF inputs.", preview_chars=preview_chars)
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, "Install pypdf to enable this parser.", preview_chars=preview_chars)

    from pypdf import PdfReader

    reader = PdfReader(str(input_path))
    parts: list[str] = []
    blocks: list[dict[str, object]] = []
    for page_index, page in enumerate(reader.pages, start=1):
        parts.append(page.extract_text() or "")
        page_height = float(getattr(page.mediabox, "height", 0) or 0)
        fragments: list[dict[str, object]] = []

        def visitor_text(text: str, _cm: Any, tm: Any, _font_dict: Any, font_size: Any) -> None:
            clean = (text or "").strip()
            if not clean:
                return
            try:
                x = float(tm[4])
                y = float(tm[5])
                size = float(font_size or 10)
            except (TypeError, ValueError, IndexError):
                return
            top = max(page_height - y, 0.0) if page_height else y
            bbox = bbox_from_values(x, top, x + max(len(clean), 1) * size * 0.5, top + size * 1.4)
            fragments.append({"text": clean, "bbox": bbox, "font_size": size})

        try:
            page.extract_text(visitor_text=visitor_text)
        except TypeError:
            fragments = []

        for fragment in _merge_fragments(fragments):
            block = make_block(
                LIBRARY_ID,
                page_index,
                "text",
                str(fragment["text"]),
                bbox=fragment["bbox"],
                provenance={
                    "source": "pypdf.extract_text(visitor_text)",
                    "coordinate_quality": "best_effort",
                },
            )
            if block:
                blocks.append(block)

    text = "\n\n".join(part for part in parts if part.strip())
    ordered_blocks = column_aware_blocks(blocks)
    column_text = blocks_to_markdown(ordered_blocks) or text
    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        column_text,
        pages=len(reader.pages),
        structured_preview=structured_preview_from_blocks(
            blocks,
            text,
            preview_chars,
            {
                "native_text_mode": "page.extract_text()",
                "structured_text_mode": "page.extract_text(visitor_text=...)",
                "coordinate_quality": "best_effort; pypdf docs warn visitor coordinates may be wrong on complicated PDFs",
            },
        ),
        preview_chars=preview_chars,
    )


def _merge_fragments(fragments: list[dict[str, object]]) -> list[dict[str, object]]:
    lines: list[dict[str, object]] = []
    for fragment in sorted(
        [item for item in fragments if item.get("bbox")],
        key=lambda item: (float(item["bbox"]["top"]), float(item["bbox"]["x0"])),
    ):
        bbox = fragment["bbox"]
        current = next((line for line in lines if abs(float(line["bbox"]["top"]) - float(bbox["top"])) <= 4), None)
        if current is None:
            lines.append({"text": str(fragment["text"]), "bbox": dict(bbox)})
            continue
        current["text"] = f"{current['text']} {fragment['text']}".strip()
        current["bbox"] = bbox_from_values(
            min(float(current["bbox"]["x0"]), float(bbox["x0"])),
            min(float(current["bbox"]["top"]), float(bbox["top"])),
            max(float(current["bbox"]["x1"]), float(bbox["x1"])),
            max(float(current["bbox"]["bottom"]), float(bbox["bottom"])),
        )
    return lines
