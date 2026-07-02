"""Layout-guided pdfplumber parser.

This parser keeps the existing pdfplumber adapter untouched. It adds a separate
layout-aware pass that uses page geometry, table bboxes, typography, and column
regions to produce evidence blocks that are better suited for field retrieval.
"""
from __future__ import annotations

import re
import statistics
import time
from dataclasses import dataclass
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
    table_sample,
)

LIBRARY_ID = "layout_pdfplumber"
DISPLAY_NAME = "Layout + pdfplumber"
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


@dataclass
class Line:
    text: str
    bbox: dict[str, float]
    word_count: int
    avg_size: float
    fontnames: list[str]


def is_available() -> bool:
    return module_available("pdfplumber")


def availability_notes() -> str | None:
    if is_available():
        return "Uses pdfplumber inside detected layout regions; original pdfplumber parser remains unchanged."
    return "Install pdfplumber to enable layout-guided pdfplumber."


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    if input_type_for(input_path) != "pdf":
        return skipped_result(LIBRARY_ID, input_path, "Layout + pdfplumber only supports PDF inputs.", preview_chars=preview_chars)
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, "Install pdfplumber to enable layout-guided pdfplumber.", preview_chars=preview_chars)

    import pdfplumber

    blocks: list[dict[str, Any]] = []
    table_count = 0
    table_samples: list[dict[str, Any]] = []
    pages_with_tables: list[int] = []
    layout_stats: list[dict[str, Any]] = []

    with pdfplumber.open(str(input_path)) as pdf:
        for page in pdf.pages:
            page_width = float(page.width)
            page_height = float(page.height)
            table_regions = _table_regions(page)
            if table_regions:
                pages_with_tables.append(page.page_number)
            for index, region in enumerate(table_regions):
                table_count += 1
                block = make_block(
                    LIBRARY_ID,
                    page.page_number,
                    "table",
                    region["markdown"],
                    bbox=region["bbox"],
                    provenance={
                        "source": "pdfplumber.find_tables",
                        "layout_role": "table",
                        "layout_region_index": index,
                        "settings": TABLE_SETTINGS,
                    },
                )
                if block:
                    blocks.append(block)
                if len(table_samples) < 5:
                    table = region["table"]
                    table_samples.append(
                        {
                            "page": page.page_number,
                            "rows": len(table),
                            "columns": max((len(row) for row in table), default=0),
                            "sample": table_sample(table),
                            "bbox": region["bbox"],
                        }
                    )

            words = page.extract_words(
                x_tolerance=1,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=False,
                extra_attrs=["fontname", "size"],
            )
            lines = [
                line
                for line in _word_lines(words)
                if not _inside_any(line.bbox, [region["bbox"] for region in table_regions])
            ]
            text_regions = _text_regions(lines, page_width, page_height)
            layout_stats.append(
                {
                    "page": page.page_number,
                    "tables": len(table_regions),
                    "text_regions": len(text_regions),
                    "layout_roles": _role_counts(text_regions),
                }
            )
            for index, region in enumerate(text_regions):
                block = make_block(
                    LIBRARY_ID,
                    page.page_number,
                    region["type"],
                    region["text"],
                    bbox=region["bbox"],
                    provenance={
                        "source": "layout_pdfplumber.region",
                        "layout_role": region["type"],
                        "layout_region_index": index,
                        "line_count": len(region["lines"]),
                        "column": region["column"],
                        "detector": "pdfplumber_geometry_typography_v1",
                    },
                    confidence=region["confidence"],
                )
                if block:
                    blocks.append(block)

        pages = len(pdf.pages)

    ordered_blocks = column_aware_blocks(blocks)
    text = blocks_to_markdown(ordered_blocks)
    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=pages,
        tables=table_count,
        images=sum(1 for block in ordered_blocks if block.get("type") in {"figure", "image", "chart"}),
        structured_preview=structured_preview_from_blocks(
            ordered_blocks,
            text,
            preview_chars,
            {
                "layout_detector": "pdfplumber_geometry_typography_v1",
                "table_settings": TABLE_SETTINGS,
                "pages_with_tables": pages_with_tables[:30],
                "pages_with_tables_count": len(pages_with_tables),
                "table_samples": table_samples,
                "layout_stats": layout_stats[:50],
                "note": "Separate parser: detects table/text/header/footer/title/list regions first, then uses pdfplumber extraction inside those regions.",
            },
        ),
        preview_chars=preview_chars,
    )


def _table_regions(page: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    found_tables = page.find_tables(table_settings=TABLE_SETTINGS) or []
    for table in found_tables:
        extracted = table.extract()
        if not _looks_like_data_table(extracted):
            continue
        bbox = bbox_from_values(*table.bbox)
        markdown = _table_to_markdown(extracted)
        if bbox and markdown:
            output.append({"bbox": bbox, "table": extracted, "markdown": markdown})
    return output


def _word_lines(words: list[dict[str, Any]]) -> list[Line]:
    grouped: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda item: (float(item.get("top") or 0), float(item.get("x0") or 0))):
        top = float(word.get("top") or 0)
        size = float(word.get("size") or 0)
        tolerance = max(2.5, size * 0.35)
        current = next((line for line in grouped if abs(float(line["top"]) - top) <= tolerance), None)
        if current is None:
            current = {"top": top, "words": []}
            grouped.append(current)
        current["words"].append(word)

    lines: list[Line] = []
    for group in grouped:
        line_words = sorted(group["words"], key=lambda item: float(item.get("x0") or 0))
        text = " ".join(str(word.get("text") or "") for word in line_words).strip()
        if not text:
            continue
        bbox = bbox_from_values(
            min(float(word.get("x0") or 0) for word in line_words),
            min(float(word.get("top") or 0) for word in line_words),
            max(float(word.get("x1") or 0) for word in line_words),
            max(float(word.get("bottom") or 0) for word in line_words),
        )
        if not bbox:
            continue
        sizes = [float(word.get("size") or 0) for word in line_words if word.get("size") is not None]
        fontnames = sorted({str(word.get("fontname") or "") for word in line_words if word.get("fontname")})
        lines.append(
            Line(
                text=text,
                bbox=bbox,
                word_count=len(line_words),
                avg_size=sum(sizes) / len(sizes) if sizes else 0.0,
                fontnames=fontnames,
            )
        )
    return lines


def _text_regions(lines: list[Line], page_width: float, page_height: float) -> list[dict[str, Any]]:
    if not lines:
        return []
    body_size = _median([line.avg_size for line in lines if line.avg_size > 0]) or 10.0
    regions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in sorted(lines, key=lambda item: (item.bbox["top"], item.bbox["x0"])):
        role = _line_role(line, body_size, page_width, page_height)
        column = _line_column(line, page_width)
        if current is None or not _same_region(current, line, role, column, body_size):
            if current:
                regions.append(_finish_region(current))
            current = {"type": role, "column": column, "lines": [line]}
        else:
            current["lines"].append(line)
    if current:
        regions.append(_finish_region(current))
    return regions


def _line_role(line: Line, body_size: float, page_width: float, page_height: float) -> str:
    text = line.text.strip()
    top = line.bbox["top"]
    bottom = line.bbox["bottom"]
    width = line.bbox["x1"] - line.bbox["x0"]
    if top <= page_height * 0.06:
        return "header"
    if bottom >= page_height * 0.94:
        return "footer"
    if _looks_like_list(text):
        return "list"
    if line.avg_size >= body_size * 1.35 and len(text) <= 140:
        return "title"
    if line.avg_size >= body_size * 1.15 and len(text) <= 160:
        return "heading"
    if _mostly_upper(text) and len(text) <= 120 and width < page_width * 0.85:
        return "heading"
    return "text"


def _same_region(current: dict[str, Any], line: Line, role: str, column: str, body_size: float) -> bool:
    if current["type"] != role or current["column"] != column:
        return False
    previous = current["lines"][-1]
    vertical_gap = line.bbox["top"] - previous.bbox["bottom"]
    if role in {"title", "heading"}:
        return vertical_gap <= max(6.0, body_size * 0.8)
    if role in {"header", "footer"}:
        return vertical_gap <= max(8.0, body_size)
    return vertical_gap <= max(10.0, body_size * 1.25)


def _finish_region(region: dict[str, Any]) -> dict[str, Any]:
    lines: list[Line] = region["lines"]
    bbox = bbox_from_values(
        min(line.bbox["x0"] for line in lines),
        min(line.bbox["top"] for line in lines),
        max(line.bbox["x1"] for line in lines),
        max(line.bbox["bottom"] for line in lines),
    )
    text = "\n".join(line.text for line in lines)
    return {
        "type": region["type"],
        "column": region["column"],
        "lines": lines,
        "bbox": bbox,
        "text": text,
        "confidence": 0.9 if region["type"] in {"title", "heading", "table"} else 0.82,
    }


def _line_column(line: Line, page_width: float) -> str:
    center = (line.bbox["x0"] + line.bbox["x1"]) / 2
    if center < page_width * 0.42:
        return "left"
    if center > page_width * 0.58:
        return "right"
    return "full"


def _inside_any(bbox: dict[str, float], regions: list[dict[str, float]]) -> bool:
    return any(_overlap_ratio(bbox, region) > 0.55 for region in regions)


def _overlap_ratio(a: dict[str, float], b: dict[str, float]) -> float:
    x0 = max(a["x0"], b["x0"])
    y0 = max(a["top"], b["top"])
    x1 = min(a["x1"], b["x1"])
    y1 = min(a["bottom"], b["bottom"])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    area = max((a["x1"] - a["x0"]) * (a["bottom"] - a["top"]), 1.0)
    return intersection / area


def _role_counts(regions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for region in regions:
        role = str(region.get("type") or "text")
        counts[role] = counts.get(role, 0) + 1
    return counts


def _table_to_markdown(table: list[list[Any]]) -> str:
    rows = [
        cleaned
        for row in table
        if row
        for cleaned in [[("" if cell is None else str(cell).replace("\n", " ").strip()) for cell in row]]
        if any(cell for cell in cleaned)
    ]
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


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.median(values)


def _looks_like_list(text: str) -> bool:
    return bool(re.match(r"^\s*(?:[-*•]|\(?\d+[\).]|[A-Za-z][\).])\s+", text))


def _mostly_upper(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 4:
        return False
    return sum(1 for char in letters if char.isupper()) / len(letters) >= 0.75


def _has_table_text(table: list[list[Any]] | None) -> bool:
    if not table:
        return False
    non_empty = 0
    for row in table:
        for cell in row or []:
            if str(cell or "").strip():
                non_empty += 1
                if non_empty >= 2:
                    return True
    return False


def _looks_like_data_table(table: list[list[Any]] | None) -> bool:
    if not _has_table_text(table):
        return False
    rows = [
        [str(cell or "").strip() for cell in row or []]
        for row in table or []
        if any(str(cell or "").strip() for cell in row or [])
    ]
    if not rows:
        return False
    populated_cells = sum(1 for row in rows for cell in row if cell)
    max_columns = max((len(row) for row in rows), default=0)
    rows_with_multiple_cells = sum(1 for row in rows if sum(1 for cell in row if cell) >= 2)
    return (len(rows) >= 2 and max_columns >= 2 and rows_with_multiple_cells >= 2) or populated_cells >= 8
