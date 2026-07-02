"""Unstructured implementation for PDF partitioning."""
from __future__ import annotations

import time
from collections import Counter
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

LIBRARY_ID = "unstructured"
DISPLAY_NAME = "Unstructured"
SUPPORTED_INPUT_TYPES = ["pdf"]


def is_available() -> bool:
    return module_available("unstructured")


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    if input_type_for(input_path) != "pdf":
        return skipped_result(
            LIBRARY_ID,
            input_path,
            "Unstructured image partitioning is intentionally disabled here to avoid OCR model downloads.",
            preview_chars=preview_chars,
        )
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, "Install unstructured[pdf] to enable this parser.", preview_chars=preview_chars)

    from unstructured.partition.pdf import partition_pdf

    strategy = "hi_res"
    try:
        elements = partition_pdf(
            filename=str(input_path),
            strategy=strategy,
            infer_table_structure=True,
        )
    except Exception:
        strategy = "fast"
        elements = partition_pdf(
            filename=str(input_path),
            strategy=strategy,
            infer_table_structure=False,
        )

    text_parts = [str(element) for element in elements if str(element).strip()]
    blocks: list[dict[str, object]] = []
    table_samples: list[dict[str, object]] = []
    category_counts = Counter(element.__class__.__name__ for element in elements)
    page_numbers = {
        getattr(getattr(element, "metadata", None), "page_number", None)
        for element in elements
    }
    page_numbers.discard(None)
    table_count = sum(
        count
        for category, count in category_counts.items()
        if "table" in category.lower()
    )
    for element in elements:
        element_type = element.__class__.__name__
        metadata = _metadata_dict(element)
        page = int(metadata.get("page_number") or 1)
        text = str(element).strip()
        block_type = element_type.lower()
        if element_type.lower() == "table":
            html = metadata.get("text_as_html")
            if html and len(table_samples) < 5:
                table_samples.append(
                    {
                        "page": page,
                        "rows": None,
                        "columns": None,
                        "sample": [[str(html)[:800]]],
                        "html": str(html)[:4000],
                    }
                )
        block = make_block(
            LIBRARY_ID,
            page,
            block_type,
            str(metadata.get("text_as_html") or text) if block_type == "table" else text,
            bbox=_bbox_from_metadata(metadata),
            provenance={
                "source": "unstructured.partition_pdf",
                "strategy": strategy,
                "element_id": getattr(element, "id", None) or getattr(element, "element_id", None),
                "metadata": _trim_metadata(metadata),
            },
            confidence=metadata.get("detection_class_prob") if isinstance(metadata.get("detection_class_prob"), float) else None,
        )
        if block:
            blocks.append(block)

    naive_text = "\n\n".join(text_parts)
    ordered_blocks = column_aware_blocks(blocks)
    text = blocks_to_markdown(ordered_blocks) or naive_text

    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=len(page_numbers),
        tables=table_count,
        structured_preview=structured_preview_from_blocks(
            blocks,
            naive_text,
            preview_chars,
            {
            "strategy": strategy,
            "element_count": len(elements),
            "category_counts": dict(category_counts),
            "table_samples": table_samples,
            },
        ),
        preview_chars=preview_chars,
    )


def _metadata_dict(element: object) -> dict[str, object]:
    metadata = getattr(element, "metadata", None)
    if not metadata:
        return {}
    if hasattr(metadata, "to_dict"):
        return metadata.to_dict()
    if isinstance(metadata, dict):
        return metadata
    return {}


def _bbox_from_metadata(metadata: dict[str, object]) -> dict[str, float] | None:
    coordinates = metadata.get("coordinates")
    if not isinstance(coordinates, dict):
        return None
    points = coordinates.get("points")
    if not points:
        return None
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
    except (TypeError, ValueError, IndexError):
        return None
    return bbox_from_values(min(xs), min(ys), max(xs), max(ys))


def _trim_metadata(metadata: dict[str, object]) -> dict[str, object]:
    keep = {
        "page_number",
        "parent_id",
        "category_depth",
        "detection_class_prob",
        "coordinates",
        "filename",
        "filetype",
        "languages",
        "links",
        "text_as_html",
    }
    trimmed = {key: value for key, value in metadata.items() if key in keep}
    if "text_as_html" in trimmed:
        trimmed["text_as_html"] = str(trimmed["text_as_html"])[:1000]
    return trimmed
