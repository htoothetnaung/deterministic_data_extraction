"""Layout-first routed parser demo using DocLayout-YOLO regions."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.models.parser_benchmark import ParserRunResult
from app.services.parsers.base import (
    bbox_from_values,
    blocks_to_markdown,
    column_aware_blocks,
    input_type_for,
    make_block,
    module_available,
    ok_result,
    project_root,
    skipped_result,
    structured_preview_from_blocks,
    table_sample,
)

LIBRARY_ID = "doclayout_yolo_demo"
DISPLAY_NAME = "DocLayout-YOLO routed demo"
SUPPORTED_INPUT_TYPES = ["pdf"]

TEXT_PARSER = "pymupdf_text_region_parser"
TABLE_PARSER = "pdfplumber_table_region_parser"
IMAGE_PARSER = "image_region_placeholder"
ERROR_PARSER = "layout_error"


def is_available() -> bool:
    return bool(_model_path()) and (module_available("doclayout_yolo") or module_available("ultralytics"))


def availability_notes() -> str | None:
    if is_available():
        return "Runs DocLayout-YOLO first, then routes each detected region to demo region parsers."
    if not _model_path():
        return "DocLayout-YOLO demo disabled: set EXTRACT_DOCLAYOUT_YOLO_MODEL_PATH to a local model file."
    return "DocLayout-YOLO demo disabled: install doclayout-yolo or ultralytics in the brillar env."


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    if input_type_for(input_path) != "pdf":
        return skipped_result(LIBRARY_ID, input_path, "DocLayout-YOLO demo only supports PDF inputs.", preview_chars=preview_chars)
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, availability_notes() or "DocLayout-YOLO demo is unavailable.", preview_chars=preview_chars)

    import fitz
    import pdfplumber

    model = _load_layout_model()
    blocks: list[dict[str, Any]] = []
    layout_stats: list[dict[str, Any]] = []
    table_samples: list[dict[str, Any]] = []
    routed_parser_names: set[str] = set()
    image_count = 0
    table_count = 0

    with tempfile.TemporaryDirectory(prefix="doclayout-yolo-demo-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        with fitz.open(str(input_path)) as fitz_doc, pdfplumber.open(str(input_path)) as plumber_doc:
            page_count = fitz_doc.page_count
            for page_index, page in enumerate(fitz_doc, start=1):
                pdf_page = plumber_doc.pages[page_index - 1] if page_index - 1 < len(plumber_doc.pages) else None
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(settings.doclayout_yolo_pdf_zoom, settings.doclayout_yolo_pdf_zoom),
                    alpha=False,
                )
                image_path = tmp_path / f"page-{page_index}.png"
                pixmap.save(str(image_path))
                try:
                    raw_result = _predict_layout(model, image_path)
                    regions = _normalize_detections(raw_result, page_index, pixmap.width, pixmap.height, page.rect.width, page.rect.height)
                except Exception as exc:  # keep overnight runs moving page-by-page
                    block = _error_block(input_path, page_index, exc)
                    if block:
                        blocks.append(block)
                        routed_parser_names.add(ERROR_PARSER)
                    layout_stats.append({"page": page_index, "regions": 0, "error": f"{exc.__class__.__name__}: {exc}"})
                    continue

                layout_stats.append({"page": page_index, "regions": len(regions), "labels": _label_counts(regions)})
                for region_index, region in enumerate(regions):
                    parser_name = _parser_for_label(region["layout_label"])
                    routed_parser_names.add(parser_name)
                    region_id = f"{LIBRARY_ID}-p{page_index}-r{region_index}"
                    try:
                        block = _parse_region(input_path, page, pdf_page, region, region_id, parser_name)
                    except Exception as exc:
                        block = _region_failure_block(input_path, region, region_id, parser_name, exc)
                    if not block:
                        continue
                    blocks.append(block)
                    if parser_name == TABLE_PARSER:
                        table_count += 1
                        if len(table_samples) < 5:
                            sample = block.get("provenance", {}).get("table_sample")
                            if sample:
                                table_samples.append(sample)
                    if parser_name == IMAGE_PARSER:
                        image_count += 1

    ordered_blocks = column_aware_blocks(blocks)
    text = blocks_to_markdown(ordered_blocks)
    metadata = _document_metadata(input_path, page_count, routed_parser_names)
    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=page_count,
        tables=table_count,
        images=image_count,
        structured_preview=structured_preview_from_blocks(
            ordered_blocks,
            text,
            preview_chars,
            {
                "document_metadata": metadata,
                "layout_model": metadata["layout_model"],
                "layout_detector": "doclayout_yolo",
                "parser_count": metadata["parser_count"],
                "parser_names": metadata["parser_names"],
                "table_samples": table_samples,
                "layout_stats": layout_stats[:100],
                "note": "Demo parser: every emitted downstream parser block has a parent DocLayout-YOLO region.",
            },
        ),
        preview_chars=preview_chars,
    )


def _model_path() -> Path | None:
    value = str(settings.doclayout_yolo_model_path or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root() / path
    return path if path.exists() else None


def _load_layout_model() -> Any:
    path = _model_path()
    if not path:
        raise RuntimeError("EXTRACT_DOCLAYOUT_YOLO_MODEL_PATH does not point to an existing model file.")
    if module_available("doclayout_yolo"):
        try:
            from doclayout_yolo import YOLOv10

            return YOLOv10(str(path))
        except Exception:
            pass
    from ultralytics import YOLO

    return YOLO(str(path))


def _predict_layout(model: Any, image_path: Path) -> Any:
    if hasattr(model, "predict"):
        result = model.predict(
            str(image_path),
            imgsz=settings.doclayout_yolo_img_size,
            conf=settings.doclayout_yolo_confidence,
            verbose=False,
        )
    else:
        result = model(str(image_path))
    if isinstance(result, list):
        return result[0] if result else None
    return result


def _normalize_detections(
    raw_result: Any,
    page_number: int,
    image_width: float,
    image_height: float,
    page_width: float,
    page_height: float,
) -> list[dict[str, Any]]:
    boxes = getattr(raw_result, "boxes", None)
    names = getattr(raw_result, "names", {}) or {}
    if boxes is None:
        return []

    xyxy_values = _as_rows(getattr(boxes, "xyxy", []))
    cls_values = _as_values(getattr(boxes, "cls", []))
    conf_values = _as_values(getattr(boxes, "conf", []))
    regions: list[dict[str, Any]] = []
    for index, coords in enumerate(xyxy_values):
        if len(coords) < 4:
            continue
        cls_id = int(cls_values[index]) if index < len(cls_values) else -1
        label = str(names.get(cls_id, cls_id if cls_id >= 0 else "unknown")).lower()
        confidence = float(conf_values[index]) if index < len(conf_values) else None
        bbox = _scale_bbox(coords[:4], image_width, image_height, page_width, page_height)
        if not bbox:
            continue
        regions.append(
            {
                "page": page_number,
                "bbox": bbox,
                "layout_label": label,
                "layout_confidence": confidence,
                "layout_region_index": index,
            }
        )
    return sorted(regions, key=lambda item: (item["bbox"]["top"], item["bbox"]["x0"]))


def _as_rows(value: Any) -> list[list[float]]:
    if hasattr(value, "detach"):
        value = value.detach().cpu().tolist()
    elif hasattr(value, "tolist"):
        value = value.tolist()
    return [[float(cell) for cell in row] for row in value]


def _as_values(value: Any) -> list[float]:
    if hasattr(value, "detach"):
        value = value.detach().cpu().tolist()
    elif hasattr(value, "tolist"):
        value = value.tolist()
    return [float(item) for item in value]


def _scale_bbox(coords: list[float], image_width: float, image_height: float, page_width: float, page_height: float) -> dict[str, float] | None:
    x0, y0, x1, y1 = coords
    return bbox_from_values(
        (x0 / image_width) * page_width,
        (y0 / image_height) * page_height,
        (x1 / image_width) * page_width,
        (y1 / image_height) * page_height,
    )


def _parser_for_label(label: str) -> str:
    lower = label.lower()
    if "table" in lower:
        return TABLE_PARSER
    if any(token in lower for token in ("figure", "image", "picture", "chart")):
        return IMAGE_PARSER
    if any(token in lower for token in ("title", "text", "plain", "paragraph", "list", "header", "footer", "caption", "section")):
        return TEXT_PARSER
    return TEXT_PARSER


def _parse_region(input_path: Path, page: Any, pdf_page: Any, region: dict[str, Any], region_id: str, parser_name: str) -> dict[str, Any] | None:
    if not region_id:
        raise ValueError("Routed parser output requires a parent layout region id.")
    bbox = region["bbox"]
    if parser_name == TABLE_PARSER and pdf_page is not None:
        cropped = pdf_page.crop((bbox["x0"], bbox["top"], bbox["x1"], bbox["bottom"]))
        table = _best_table(cropped.extract_tables() or [])
        markdown = _table_to_markdown(table) if table else ""
        text = markdown or (cropped.extract_text(x_tolerance=1, y_tolerance=3) or "")
        provenance_extra: dict[str, Any] = {}
        if table:
            provenance_extra["table_sample"] = {
                "page": region["page"],
                "rows": len(table),
                "columns": max((len(row) for row in table), default=0),
                "sample": table_sample(table),
                "bbox": bbox,
            }
        return _routed_block(input_path, region, region_id, "table", text, parser_name, provenance_extra)
    if parser_name == IMAGE_PARSER:
        return _routed_block(input_path, region, region_id, "image", f"[{region['layout_label']} region]", parser_name, {})
    clip = fitz_rect_from_bbox(bbox)
    text = page.get_text("text", clip=clip, sort=True) or ""
    return _routed_block(input_path, region, region_id, "text", text, parser_name, {})


def _routed_block(
    input_path: Path,
    region: dict[str, Any],
    region_id: str,
    block_type: str,
    text: str,
    parser_name: str,
    provenance_extra: dict[str, Any],
) -> dict[str, Any] | None:
    block = make_block(
        LIBRARY_ID,
        int(region["page"]),
        block_type,
        text,
        bbox=region["bbox"],
        provenance={
            "source": parser_name,
            "parent_layout_region_id": region_id,
            "layout_label": region["layout_label"],
            "layout_confidence": region["layout_confidence"],
            "layout_region_index": region["layout_region_index"],
            "filename": input_path.name,
            **provenance_extra,
        },
        confidence=region["layout_confidence"],
    )
    if block:
        block["layout_label"] = region["layout_label"]
        block["layout_confidence"] = region["layout_confidence"]
        block["routed_parser"] = parser_name
    return block


def fitz_rect_from_bbox(bbox: dict[str, float]) -> Any:
    import fitz

    return fitz.Rect(bbox["x0"], bbox["top"], bbox["x1"], bbox["bottom"])


def _best_table(tables: list[list[list[Any]]]) -> list[list[Any]]:
    return max(tables, key=lambda table: len(table) * max((len(row) for row in table), default=0), default=[])


def _table_to_markdown(table: list[list[Any]]) -> str:
    rows = [["" if cell is None else str(cell).replace("\n", " ").strip() for cell in row] for row in table if row]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    return "\n".join(
        ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        + ["| " + " | ".join(row) + " |" for row in rows[1:]]
    )


def _error_block(input_path: Path, page_number: int, exc: Exception) -> dict[str, Any] | None:
    return make_block(
        LIBRARY_ID,
        page_number,
        "unknown",
        f"Layout detection failed on page {page_number}: {exc.__class__.__name__}: {exc}",
        provenance={"source": ERROR_PARSER, "filename": input_path.name},
    )


def _region_failure_block(input_path: Path, region: dict[str, Any], region_id: str, parser_name: str, exc: Exception) -> dict[str, Any] | None:
    return _routed_block(
        input_path,
        region,
        region_id,
        "unknown",
        f"Region parser failed: {parser_name}: {exc.__class__.__name__}: {exc}",
        parser_name,
        {"error": f"{exc.__class__.__name__}: {exc}"},
    )


def _document_metadata(input_path: Path, page_count: int, parser_names: set[str]) -> dict[str, Any]:
    names = sorted(name for name in parser_names if name != ERROR_PARSER)
    return {
        "filename": input_path.name,
        "document_title": input_path.stem.replace("_", " ").replace("-", " ").strip() or input_path.name,
        "page_count": page_count,
        "parser_count": len(names),
        "parser_names": names,
        "layout_model": str(_model_path() or ""),
    }


def _label_counts(regions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for region in regions:
        label = str(region.get("layout_label") or "unknown")
        counts[label] = counts.get(label, 0) + 1
    return counts
