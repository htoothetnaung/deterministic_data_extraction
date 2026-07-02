"""pdfplumber implementation for PDF text and table extraction."""
from __future__ import annotations

import time
from pathlib import Path

from app.models.parser_benchmark import ParserRunResult
from app.services.parsers.base import (
    bbox_from_values,
    blocks_to_markdown,
    column_aware_blocks,
    make_block,
    input_type_for,
    module_available,
    ok_result,
    skipped_result,
    structured_preview_from_blocks,
    table_sample,
)

LIBRARY_ID = "pdfplumber"
DISPLAY_NAME = "pdfplumber"
SUPPORTED_INPUT_TYPES = ["pdf"]

TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "min_words_vertical": 3,
    "min_words_horizontal": 1,
    "intersection_tolerance": 3,
    "text_tolerance": 3,
}


def is_available() -> bool:
    return module_available("pdfplumber")


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    if input_type_for(input_path) != "pdf":
        return skipped_result(LIBRARY_ID, input_path, "pdfplumber only supports PDF inputs.", preview_chars=preview_chars)
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, "Install pdfplumber to enable this parser.", preview_chars=preview_chars)

    import pdfplumber

    text_parts: list[str] = []
    blocks: list[dict[str, object]] = []
    table_count = 0
    table_samples: list[dict[str, object]] = []
    pages_with_tables: list[int] = []

    with pdfplumber.open(str(input_path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            if page_text.strip():
                text_parts.append(page_text)

            words = page.extract_words(
                x_tolerance=1,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=False,
                extra_attrs=["fontname", "size"],
            )
            for line in _word_lines(words):
                block = make_block(
                    LIBRARY_ID,
                    page.page_number,
                    "text",
                    line["text"],
                    bbox=line["bbox"],
                    provenance={
                        "source": "pdfplumber.extract_words",
                        "word_count": line["word_count"],
                    },
                )
                if block:
                    blocks.append(block)

            found_tables = page.find_tables(table_settings=TABLE_SETTINGS) or []
            tables = [table.extract() for table in found_tables]
            if tables:
                pages_with_tables.append(page.page_number)
            for index, table in enumerate(tables):
                if table:
                    table_count += 1
                    table_bbox = bbox_from_values(*found_tables[index].bbox) if index < len(found_tables) else None
                    table_md = _table_to_markdown(table)
                    block = make_block(
                        LIBRARY_ID,
                        page.page_number,
                        "table",
                        table_md,
                        bbox=table_bbox,
                        provenance={
                            "source": "pdfplumber.find_tables",
                            "settings": TABLE_SETTINGS,
                        },
                    )
                    if block:
                        blocks.append(block)
                    if len(table_samples) < 5:
                        table_samples.append(
                            {
                                "page": page.page_number,
                                "rows": len(table),
                                "columns": max((len(row) for row in table), default=0),
                                "sample": table_sample(table),
                            }
                        )

        pages = len(pdf.pages)

    naive_text = "\n\n".join(text_parts)
    ordered_blocks = column_aware_blocks(blocks)
    text = blocks_to_markdown(ordered_blocks) or naive_text

    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=pages,
        tables=table_count,
        structured_preview=structured_preview_from_blocks(
            blocks,
            naive_text,
            preview_chars,
            {
            "table_settings": TABLE_SETTINGS,
            "pages_with_tables": pages_with_tables[:30],
            "pages_with_tables_count": len(pages_with_tables),
            "table_samples": table_samples,
            },
        ),
        preview_chars=preview_chars,
    )


def _word_lines(words: list[dict[str, object]]) -> list[dict[str, object]]:
    lines: list[dict[str, object]] = []
    for word in sorted(words, key=lambda item: (float(item.get("top") or 0), float(item.get("x0") or 0))):
        top = float(word.get("top") or 0)
        current = next((line for line in lines if abs(float(line["top"]) - top) <= 3), None)
        if current is None:
            current = {"top": top, "words": []}
            lines.append(current)
        current["words"].append(word)

    output: list[dict[str, object]] = []
    for line in lines:
        line_words = sorted(line["words"], key=lambda item: float(item.get("x0") or 0))
        text = " ".join(str(word.get("text") or "") for word in line_words).strip()
        bbox = bbox_from_values(
            min(float(word.get("x0") or 0) for word in line_words),
            min(float(word.get("top") or 0) for word in line_words),
            max(float(word.get("x1") or 0) for word in line_words),
            max(float(word.get("bottom") or 0) for word in line_words),
        )
        if text and bbox:
            output.append({"text": text, "bbox": bbox, "word_count": len(line_words)})
    return output


def _table_to_markdown(table: list[list[object]]) -> str:
    rows = [["" if cell is None else str(cell).replace("\n", " ").strip() for cell in row] for row in table if row]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    separator = ["---"] * width
    body = rows[1:]
    return "\n".join(
        ["| " + " | ".join(header) + " |", "| " + " | ".join(separator) + " |"]
        + ["| " + " | ".join(row) + " |" for row in body]
    )
