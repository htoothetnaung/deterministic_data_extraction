"""Docling parser using Atenxion service-style conversion options."""
from __future__ import annotations

import time
import json
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.models.parser_benchmark import ParserRunResult
from app.services.parsers.base import (
    bbox_from_values,
    blocks_to_markdown,
    column_aware_blocks,
    input_type_for,
    failed_result,
    make_block,
    module_available,
    ok_result,
    preview_text,
    project_root,
    skipped_result,
    structured_preview_from_blocks,
)

LIBRARY_ID = "docling"
DISPLAY_NAME = "Docling"
SUPPORTED_INPUT_TYPES = ["pdf"]


def is_available() -> bool:
    return module_available("docling") or bool(_service_url())


def availability_notes() -> str | None:
    if _service_url():
        return f"Uses Docling-compatible service endpoint at {_service_url()} with Atenxion Docling options."
    if module_available("docling"):
        return "Uses local Docling with Atenxion Docling service-style options."
    return "Install docling or set EXTRACT_DOCLING_SERVICE_URL to a Docling-compatible service."


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    if input_type_for(input_path) != "pdf":
        return skipped_result(LIBRARY_ID, input_path, "Docling is configured here for PDF inputs only.", preview_chars=preview_chars)
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, availability_notes() or "Docling is unavailable.", preview_chars=preview_chars)

    if _service_url():
        return _parse_via_service(input_path, start, preview_chars)
    return _parse_locally(input_path, start, preview_chars)


def _parse_locally(input_path: Path, start: float, preview_chars: int) -> ParserRunResult:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import AcceleratorOptions, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.document_timeout = settings.docling_timeout_seconds
    options.accelerator_options = AcceleratorOptions(
        num_threads=settings.docling_accelerator_threads,
        device=settings.docling_accelerator_device,
    )
    options.do_table_structure = settings.docling_do_table_structure
    options.do_ocr = settings.docling_do_ocr
    options.force_backend_text = settings.docling_force_backend_text
    options.generate_page_images = settings.docling_generate_page_images
    options.generate_picture_images = settings.docling_generate_picture_images
    options.generate_table_images = settings.docling_generate_table_images

    artifacts_path = _docling_artifacts_path()
    if artifacts_path:
        options.artifacts_path = artifacts_path

    _apply_ocr_options(options)

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        }
    )
    max_pages = settings.docling_max_pages
    result = converter.convert(
        input_path,
        raises_on_error=False,
        max_num_pages=max_pages if max_pages > 0 else 9223372036854775807,
    )
    document = result.document
    naive_text = _export_markdown(document)
    document_dict = _document_to_dict(document)
    blocks = _blocks_from_document_dict(document_dict)
    ordered_blocks = column_aware_blocks(blocks)
    text = blocks_to_markdown(ordered_blocks) or naive_text
    conversion_status = str(getattr(getattr(result, "status", None), "value", getattr(result, "status", "")) or "")
    if conversion_status.lower() == "failure" and not text.strip():
        return failed_result(
            LIBRARY_ID,
            input_path,
            RuntimeError(_conversion_failure_message(result)),
            seconds=time.perf_counter() - start,
            preview_chars=preview_chars,
        )

    pages = len(getattr(document, "pages", {}) or {})
    table_count = len(getattr(document, "tables", []) or [])
    picture_count = len(getattr(document, "pictures", []) or [])

    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=pages,
        tables=table_count,
        images=picture_count,
        structured_preview=structured_preview_from_blocks(
            ordered_blocks,
            naive_text,
            preview_chars,
            {
                "mode": "local",
                "docling_options": _docling_options_payload(),
                "artifacts_path": str(artifacts_path) if artifacts_path else None,
                "docling_schema_keys": sorted(document_dict.keys())[:30],
                "conversion_status": conversion_status,
                "conversion_errors": _conversion_errors(result),
                "raw_item_counts": _raw_item_counts(document_dict),
                "exported_formats": _to_formats(),
                "note": "Local Docling is configured to match the Atenxion Docling service option shape.",
            },
        ),
        preview_chars=preview_chars,
    )


def _parse_via_service(input_path: Path, start: float, preview_chars: int) -> ParserRunResult:
    if not module_available("requests"):
        return skipped_result(LIBRARY_ID, input_path, "Install requests to use EXTRACT_DOCLING_SERVICE_URL.", preview_chars=preview_chars)

    import requests

    url = _service_url().rstrip("/")
    service_kind = "atenxion-extract" if url.rstrip("/").endswith("/extract") else "docling-convert"
    if service_kind == "docling-convert" and not url.endswith("/v1/convert/file"):
        url = f"{url}/v1/convert/file"

    with input_path.open("rb") as handle:
        response = requests.post(
            url,
            files=[("files", (input_path.name, handle, "application/pdf"))],
            data=_form_options_payload(),
            timeout=settings.docling_timeout_seconds,
        )
    response.raise_for_status()
    payload = _service_response_payload(response, service_kind)

    document_payload = _service_document_payload(payload)
    text = _service_text(payload)
    blocks = _blocks_from_service_payload(document_payload)
    ordered_blocks = column_aware_blocks(blocks)
    final_text = blocks_to_markdown(ordered_blocks) or text
    raw_counts = _raw_item_counts(document_payload)

    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        final_text,
        pages=_service_page_count(document_payload, blocks),
        tables=raw_counts["tables"],
        images=raw_counts["pictures"],
        structured_preview=structured_preview_from_blocks(
            ordered_blocks,
            text,
            preview_chars,
            {
                "mode": "service",
                "service_kind": service_kind,
                "service_url": url,
                "docling_options": _docling_options_payload(),
                "raw_item_counts": raw_counts,
                "service_keys": sorted(payload.keys())[:30] if isinstance(payload, dict) else [],
                "document_keys": sorted(document_payload.keys())[:30],
                "exported_formats": _to_formats(),
            },
        ),
        preview_chars=preview_chars,
    )


def _docling_artifacts_path() -> Path | None:
    cache_path = project_root() / ".cache" / "docling" / "models"
    return cache_path if cache_path.exists() else None


def _service_url() -> str:
    return str(settings.docling_service_url or "").strip()


def _to_formats() -> list[str]:
    return [part.strip() for part in settings.docling_to_formats.split(",") if part.strip()]


def _docling_options_payload() -> dict[str, Any]:
    return {
        "do_table_structure": settings.docling_do_table_structure,
        "image_export_mode": settings.docling_image_export_mode,
        "do_ocr": settings.docling_do_ocr,
        "force_ocr": settings.docling_force_ocr,
        "ocr_engine": settings.docling_ocr_engine,
        "ocr_lang": [part.strip() for part in settings.docling_ocr_lang.split(",") if part.strip()],
        "to_formats": _to_formats(),
        "pipeline": settings.docling_pipeline,
        "max_pages": settings.docling_max_pages,
        "generate_page_images": settings.docling_generate_page_images,
        "generate_picture_images": settings.docling_generate_picture_images,
        "generate_table_images": settings.docling_generate_table_images,
        "force_backend_text": settings.docling_force_backend_text,
        "accelerator_device": settings.docling_accelerator_device,
        "accelerator_threads": settings.docling_accelerator_threads,
    }


def _form_options_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in _docling_options_payload().items():
        if key == "max_pages":
            continue
        if isinstance(value, bool):
            payload[key] = str(value).lower()
        elif isinstance(value, list):
            payload[key] = value[0] if len(value) == 1 else value
        else:
            payload[key] = str(value)
    return payload


def _service_response_payload(response: Any, service_kind: str) -> dict[str, Any]:
    if service_kind == "docling-convert":
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    result: dict[str, Any] = {}
    for line in response.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result = item
            if isinstance(item.get("result"), dict):
                result = item["result"]
                break
    return result


def _apply_ocr_options(options: Any) -> None:
    if not settings.docling_do_ocr and not settings.docling_force_ocr:
        return

    engine = settings.docling_ocr_engine.lower().strip()
    langs = [part.strip() for part in settings.docling_ocr_lang.split(",") if part.strip()] or ["eng"]

    try:
        if engine in {"tesseract", "tesserocr"}:
            from docling.datamodel.pipeline_options import TesseractCliOcrOptions

            options.ocr_options = TesseractCliOcrOptions(lang=langs, force_full_page_ocr=settings.docling_force_ocr)
        elif engine == "rapidocr":
            from docling.datamodel.pipeline_options import RapidOcrOptions

            options.ocr_options = RapidOcrOptions(lang=langs, force_full_page_ocr=settings.docling_force_ocr)
        else:
            from docling.datamodel.pipeline_options import EasyOcrOptions

            options.ocr_options = EasyOcrOptions(lang=langs, force_full_page_ocr=settings.docling_force_ocr)
    except Exception:
        return


def _export_markdown(document: object) -> str:
    method = getattr(document, "export_to_markdown", None)
    if not callable(method):
        return ""
    try:
        return method(image_mode=_image_ref_mode())
    except TypeError:
        try:
            return method()
        except Exception:
            return ""


def _image_ref_mode() -> Any:
    try:
        from docling_core.types.doc import ImageRefMode
    except Exception:
        return settings.docling_image_export_mode

    mode = settings.docling_image_export_mode.lower().strip()
    if mode == "embedded":
        return ImageRefMode.EMBEDDED
    if mode == "referenced":
        return ImageRefMode.REFERENCED
    return ImageRefMode.PLACEHOLDER


def _document_to_dict(document: object) -> dict[str, Any]:
    for method_name in ("export_to_dict", "model_dump", "dict"):
        method = getattr(document, method_name, None)
        if callable(method):
            try:
                payload = method()
                if isinstance(payload, dict):
                    return payload
            except TypeError:
                continue
    return {}


def _blocks_from_document_dict(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for collection_name, fallback_type in (("texts", "text"), ("tables", "table"), ("pictures", "image")):
        for item in payload.get(collection_name, []) or []:
            if not isinstance(item, dict):
                continue
            text = _item_text(item, fallback_type)
            page, bbox = _item_provenance(item)
            block = make_block(
                LIBRARY_ID,
                page,
                str(item.get("label") or fallback_type).lower(),
                text,
                bbox=bbox,
                provenance={
                    "source": "docling.DoclingDocument",
                    "collection": collection_name,
                    "self_ref": item.get("self_ref"),
                    "parent": item.get("parent"),
                    "prov": item.get("prov"),
                },
            )
            if block:
                blocks.append(block)
    return blocks


def _blocks_from_service_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    if any(key in payload for key in ("texts", "tables", "pictures")):
        return _blocks_from_document_dict(payload)
    return []


def _item_text(item: dict[str, Any], fallback_type: str) -> str:
    if isinstance(item.get("text"), str):
        return item["text"]
    if isinstance(item.get("caption"), str):
        return item["caption"]
    if isinstance(item.get("data"), dict):
        table_markdown = _table_data_to_markdown(item["data"])
        if table_markdown:
            return table_markdown
        table = item["data"].get("table_cells") or item["data"].get("grid")
        if table:
            return preview_text(str(table), 4000)
    return f"[{fallback_type}]"


def _table_data_to_markdown(data: dict[str, Any]) -> str:
    cells = data.get("table_cells")
    if not isinstance(cells, list) or not cells:
        return ""

    grid: dict[tuple[int, int], str] = {}
    max_row = 0
    max_col = 0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        row = int(cell.get("start_row_offset_idx", cell.get("row", 0)) or 0)
        col = int(cell.get("start_col_offset_idx", cell.get("col", 0)) or 0)
        max_row = max(max_row, row)
        max_col = max(max_col, col)
        grid[(row, col)] = str(cell.get("text") or "").strip()

    rows = [[grid.get((row, col), "") for col in range(max_col + 1)] for row in range(max_row + 1)]
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return ""

    header = rows[0]
    separator = ["---"] * len(header)
    body = rows[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _item_provenance(item: dict[str, Any]) -> tuple[int, dict[str, float] | None]:
    prov = item.get("prov")
    if isinstance(prov, list) and prov:
        first = prov[0]
    elif isinstance(prov, dict):
        first = prov
    else:
        first = {}
    page = int(first.get("page_no") or first.get("page") or 1) if isinstance(first, dict) else 1
    bbox_payload = first.get("bbox") if isinstance(first, dict) else None
    return page, _docling_bbox(bbox_payload)


def _docling_bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    left = value.get("l", value.get("left", value.get("x0")))
    top = value.get("t", value.get("top", value.get("y0")))
    right = value.get("r", value.get("right", value.get("x1")))
    bottom = value.get("b", value.get("bottom", value.get("y1")))
    return bbox_from_values(left, top, right, bottom)


def _raw_item_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {
        "texts": len(payload.get("texts", []) or []) if isinstance(payload, dict) else 0,
        "tables": len(payload.get("tables", []) or []) if isinstance(payload, dict) else 0,
        "pictures": len(payload.get("pictures", []) or []) if isinstance(payload, dict) else 0,
    }


def _conversion_errors(result: Any) -> list[str]:
    errors = getattr(result, "errors", None)
    if not errors:
        return []
    return [str(error) for error in errors]


def _conversion_failure_message(result: Any) -> str:
    input_doc = getattr(result, "input", None)
    valid = getattr(input_doc, "valid", None)
    page_count = getattr(input_doc, "page_count", None)
    errors = _conversion_errors(result)
    details = []
    if valid is not None:
        details.append(f"valid={valid}")
    if page_count is not None:
        details.append(f"page_count={page_count}")
    if errors:
        details.append("errors=" + "; ".join(errors[:3]))
    suffix = f" ({', '.join(details)})" if details else ""
    return f"Docling conversion failed before emitting content{suffix}"


def _service_document_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("document", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            if isinstance(value.get("document"), dict):
                return value["document"]
            return value
    return payload


def _service_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    document = _service_document_payload(payload)
    for source in (payload, document):
        for key in ("md_content", "markdown", "text", "content"):
            value = source.get(key) if isinstance(source, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _service_page_count(payload: dict[str, Any], blocks: list[dict[str, Any]]) -> int:
    pages = payload.get("pages") if isinstance(payload, dict) else None
    if isinstance(pages, dict):
        return len(pages)
    if isinstance(pages, list):
        return len(pages)
    return max((int(block.get("page") or 1) for block in blocks), default=1)
