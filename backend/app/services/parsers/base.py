"""Shared helpers for parser benchmark services."""
from __future__ import annotations

import importlib.util
import hashlib
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.models.parser_benchmark import ParserInputInfo, ParserRunResult, ParserStatus

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
DOCUMENT_EXTENSIONS = {".doc", ".docx"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json"}
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS | TEXT_EXTENSIONS


def project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def data_dir() -> Path:
    return project_root() / "data"


def upload_dir() -> Path:
    return Path(settings.upload_dir)


def module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def input_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in DOCUMENT_EXTENSIONS:
        return "document"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    return "unknown"


def _collect_inputs(root: Path, prefix: str | None = None) -> list[ParserInputInfo]:
    inputs: list[ParserInputInfo] = []
    if not root.exists():
        return inputs
    for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        input_id = f"{prefix}:{path.name}" if prefix else path.name
        inputs.append(
            ParserInputInfo(
                id=input_id,
                name=path.name,
                input_type=input_type_for(path),
                size_bytes=path.stat().st_size,
                path=str(path),
                page_count=page_count_for(path),
            )
        )
    return inputs


def list_parser_inputs() -> list[ParserInputInfo]:
    inputs = _collect_inputs(data_dir())
    existing_ids = {item.id for item in inputs}
    for upload_input in _collect_inputs(upload_dir(), prefix="upload"):
        if upload_input.id not in existing_ids:
            inputs.append(upload_input)
    return inputs


def resolve_input(input_id: str) -> ParserInputInfo | None:
    for item in list_parser_inputs():
        if item.id == input_id:
            return item
    return None


def page_count_for(path: Path) -> int:
    input_type = input_type_for(path)
    if input_type == "pdf":
        try:
            import fitz

            with fitz.open(str(path)) as document:
                return max(int(document.page_count), 1)
        except Exception:
            return 1
    return 1


def preview_text(text: str, max_chars: int) -> str:
    compact = text.strip()
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars].rstrip()}..."


def table_sample(table: list[list[Any]], max_rows: int = 8, max_cols: int = 8) -> list[list[str]]:
    rows = table[:max_rows]
    sampled: list[list[str]] = []
    for row in rows:
        cells = row[:max_cols]
        sampled.append(["" if cell is None else str(cell) for cell in cells])
    return sampled


def bbox_from_values(x0: Any, top: Any, x1: Any, bottom: Any) -> dict[str, float] | None:
    try:
        values = {
            "x0": float(x0),
            "top": float(top),
            "x1": float(x1),
            "bottom": float(bottom),
        }
    except (TypeError, ValueError):
        return None
    if values["x1"] <= values["x0"] or values["bottom"] <= values["top"]:
        return None
    return values


def make_block(
    library: str,
    page: int,
    block_type: str,
    text: str,
    bbox: dict[str, float] | None = None,
    provenance: dict[str, Any] | None = None,
    confidence: float | None = None,
) -> dict[str, Any] | None:
    clean = (text or "").strip()
    if not clean and block_type not in {"image", "table"}:
        return None
    digest = hashlib.sha1(f"{library}|{page}|{block_type}|{clean[:120]}|{bbox}".encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"{library}-p{page}-{block_type}-{digest}",
        "page": page,
        "type": block_type or "text",
        "text": clean,
        "text_preview": preview_text(clean, 1200),
        "bbox": bbox,
        "confidence": confidence,
        "provenance": provenance or {},
    }


def column_aware_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for page in sorted({int(block.get("page") or 1) for block in blocks}):
        page_blocks = [block for block in blocks if int(block.get("page") or 1) == page]
        ordered.extend(_column_aware_page_blocks(page_blocks))
    return ordered


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    current_page: int | None = None
    for block in blocks:
        page = int(block.get("page") or 1)
        if page != current_page:
            current_page = page
            parts.append(f"<!-- page: {page} -->")
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        block_type = str(block.get("type") or "text").lower()
        if block_type in {"title", "heading", "sectionheader"} and not text.startswith("#"):
            parts.append(f"## {text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)


def structured_preview_from_blocks(
    blocks: list[dict[str, Any]],
    naive_text: str,
    preview_chars: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ordered = column_aware_blocks(blocks)
    pages: list[dict[str, Any]] = []
    for page in sorted({int(block.get("page") or 1) for block in ordered}):
        page_blocks = [block for block in ordered if int(block.get("page") or 1) == page]
        pages.append(
            {
                "page": page,
                "blocks": len(page_blocks),
                "text_preview": preview_text(blocks_to_markdown(page_blocks), min(preview_chars, 2000)),
            }
        )
    payload = {
        "reading_order": "column_aware",
        "naive_text_preview": preview_text(naive_text, min(preview_chars, 2000)),
        "pages": pages,
        "blocks": ordered,
        "block_samples": [
            {
                "page": block.get("page"),
                "type": block.get("type"),
                "bbox": block.get("bbox"),
                "text_preview": block.get("text_preview"),
                "provenance": block.get("provenance"),
            }
            for block in ordered[:12]
        ],
    }
    if extra:
        payload.update(extra)
    return payload


def _column_aware_page_blocks(page_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with_bbox = [block for block in page_blocks if isinstance(block.get("bbox"), dict)]
    if len(with_bbox) < 6:
        return sorted(page_blocks, key=_block_sort_key)

    page_width = max(float(block["bbox"]["x1"]) for block in with_bbox)
    centers = sorted((float(block["bbox"]["x0"]) + float(block["bbox"]["x1"])) / 2 for block in with_bbox)
    gaps = [(centers[index + 1] - centers[index], index) for index in range(len(centers) - 1)]
    gap, index = max(gaps, default=(0.0, 0))
    min_gap = max(45.0, page_width * 0.08)
    if gap < min_gap:
        return sorted(page_blocks, key=_block_sort_key)

    split_x = (centers[index] + centers[index + 1]) / 2
    sorted_blocks = sorted(page_blocks, key=_block_sort_key)
    output: list[dict[str, Any]] = []
    segment: list[dict[str, Any]] = []

    def flush_segment() -> None:
        if not segment:
            return
        left: list[dict[str, Any]] = []
        right: list[dict[str, Any]] = []
        no_bbox: list[dict[str, Any]] = []
        for block in segment:
            bbox = block.get("bbox")
            if not isinstance(bbox, dict):
                no_bbox.append(block)
                continue
            center = (float(bbox["x0"]) + float(bbox["x1"])) / 2
            (left if center < split_x else right).append(block)
        output.extend(sorted(left, key=_block_sort_key))
        output.extend(sorted(right, key=_block_sort_key))
        output.extend(sorted(no_bbox, key=_block_sort_key))
        segment.clear()

    for block in sorted_blocks:
        bbox = block.get("bbox")
        if isinstance(bbox, dict) and float(bbox["x1"]) - float(bbox["x0"]) > page_width * 0.65:
            flush_segment()
            output.append(block)
        else:
            segment.append(block)
    flush_segment()
    return output


def _block_sort_key(block: dict[str, Any]) -> tuple[float, float]:
    bbox = block.get("bbox")
    if isinstance(bbox, dict):
        return float(bbox.get("top") or 0), float(bbox.get("x0") or 0)
    return 0.0, 0.0


def skipped_result(
    library: str,
    input_path: Path,
    reason: str,
    seconds: float = 0.0,
    preview_chars: int = 1500,
) -> ParserRunResult:
    return ParserRunResult(
        library=library,
        input_file=input_path.name,
        input_type=input_type_for(input_path),
        status=ParserStatus.SKIPPED,
        seconds=round(seconds, 4),
        pages=0,
        chars=0,
        tables=0,
        images=0,
        error=reason,
        text_preview=preview_text(reason, preview_chars),
        raw_text=reason,
    )


def failed_result(
    library: str,
    input_path: Path,
    error: Exception,
    seconds: float,
    preview_chars: int = 1500,
) -> ParserRunResult:
    message = f"{error.__class__.__name__}: {error}"
    return ParserRunResult(
        library=library,
        input_file=input_path.name,
        input_type=input_type_for(input_path),
        status=ParserStatus.FAILED,
        seconds=round(seconds, 4),
        pages=0,
        chars=0,
        tables=0,
        images=0,
        error=message,
        text_preview=preview_text(message, preview_chars),
        raw_text=message,
    )


def ok_result(
    library: str,
    input_path: Path,
    seconds: float,
    text: str,
    pages: int,
    tables: int = 0,
    images: int = 0,
    structured_preview: dict[str, Any] | None = None,
    preview_chars: int = 1500,
) -> ParserRunResult:
    return ParserRunResult(
        library=library,
        input_file=input_path.name,
        input_type=input_type_for(input_path),
        status=ParserStatus.OK,
        seconds=round(seconds, 4),
        pages=pages,
        chars=len(text),
        tables=tables,
        images=images,
        error=None,
        text_preview=preview_text(text, preview_chars),
        structured_preview=structured_preview or {},
        raw_text=text,
    )
