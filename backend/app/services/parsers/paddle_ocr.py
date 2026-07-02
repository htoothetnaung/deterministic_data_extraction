"""PaddleOCR local parser adapter.

PaddleOCR gives strong text detection and recognition. This adapter turns its
line-level OCR output into the same block/Markdown evidence shape used by the
parser and extraction labs, adding reading order, page markers, bboxes, and
confidence metadata so extraction has structure to retrieve from.
"""
from __future__ import annotations

import tempfile
import time
import os
import ssl
import logging
from collections.abc import Iterator
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
    skipped_result,
    structured_preview_from_blocks,
)

LIBRARY_ID = "paddle_ocr"
DISPLAY_NAME = "PaddleOCR local"
SUPPORTED_INPUT_TYPES = ["pdf", "image"]
logger = logging.getLogger("uvicorn.error")


def is_available() -> bool:
    return module_available("paddleocr")


def availability_notes() -> str | None:
    if is_available():
        return "Uses local PaddleOCR for detection and recognition, then normalizes OCR lines into layout blocks."
    return "Install paddleocr and paddlepaddle to enable local PaddleOCR."


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    input_type = input_type_for(input_path)
    _configure_cache()
    _configure_ssl_context()
    if input_type not in SUPPORTED_INPUT_TYPES:
        return skipped_result(
            LIBRARY_ID,
            input_path,
            "PaddleOCR local supports PDF and image inputs.",
            preview_chars=preview_chars,
        )
    if not is_available():
        return skipped_result(
            LIBRARY_ID,
            input_path,
            "Install paddleocr and paddlepaddle to enable local PaddleOCR.",
            preview_chars=preview_chars,
        )

    from paddleocr import PaddleOCR

    device, device_note = _resolve_device()
    ocr = _make_ocr(PaddleOCR)
    blocks: list[dict[str, Any]] = []
    page_summaries: list[dict[str, Any]] = []
    source_page_count = 1
    with tempfile.TemporaryDirectory(prefix="paddleocr-pages-") as tmp:
        page_images = _page_images(input_path, input_type, Path(tmp), settings.paddleocr_max_pages)
        for page_number, image_path, source_page_count in page_images:
            logger.info(
                "[paddle-ocr] OCR page started input=%s page=%s/%s max_pages=%s",
                input_path.name,
                page_number,
                source_page_count,
                settings.paddleocr_max_pages,
            )
            raw = _run_ocr(ocr, image_path)
            lines = _extract_lines(raw)
            page_blocks = _lines_to_blocks(lines, page_number)
            blocks.extend(page_blocks)
            page_summaries.append(
                {
                    "page": page_number,
                    "lines": len(lines),
                    "avg_confidence": _avg_confidence(lines),
                    "image_path": str(image_path),
                }
            )
            logger.info(
                "[paddle-ocr] OCR page finished input=%s page=%s lines=%s",
                input_path.name,
                page_number,
                len(lines),
            )

    ordered_blocks = column_aware_blocks(blocks)
    text = blocks_to_markdown(ordered_blocks)
    processed_pages = max((page["page"] for page in page_summaries), default=1)
    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=processed_pages,
        tables=sum(1 for block in ordered_blocks if block.get("type") == "table"),
        images=0,
        structured_preview=structured_preview_from_blocks(
            ordered_blocks,
            text,
            preview_chars,
            {
                "ocr_engine": "paddleocr",
                "language": settings.paddleocr_lang,
                "use_gpu": settings.paddleocr_use_gpu,
                "device": device,
                "device_note": device_note,
                "page_summaries": page_summaries,
                "source_pages": source_page_count,
                "processed_pages": processed_pages,
                "max_pages": settings.paddleocr_max_pages,
                "truncated": input_type == "pdf" and processed_pages < source_page_count,
                "note": "Local PaddleOCR recognizes text lines. Parser Lab caps PDF OCR pages by EXTRACT_PADDLEOCR_MAX_PAGES; layout blocks and Markdown are reconstructed for retrieval/extraction.",
            },
        ),
        preview_chars=preview_chars,
    )


def _make_ocr(PaddleOCR: Any) -> Any:
    device, _ = _resolve_device()
    return PaddleOCR(
        lang=settings.paddleocr_lang,
        device=device,
        use_doc_orientation_classify=True,
        use_doc_unwarping=False,
        use_textline_orientation=True,
    )


def _resolve_device() -> tuple[str, str]:
    configured = str(settings.paddleocr_device or "").strip()
    if configured:
        return configured, "Using EXTRACT_PADDLEOCR_DEVICE."
    if not settings.paddleocr_use_gpu:
        return "cpu", "Using CPU because EXTRACT_PADDLEOCR_USE_GPU is false."
    try:
        import paddle
    except ImportError:
        return "cpu", "Using CPU because paddle is not importable."
    if not paddle.device.is_compiled_with_cuda():
        return "cpu", "Using CPU because installed paddlepaddle is not compiled with CUDA."
    try:
        if paddle.device.cuda.device_count() <= 0:
            return "cpu", "Using CPU because no CUDA device is visible."
    except Exception as exc:
        return "cpu", f"Using CPU because CUDA device check failed: {exc.__class__.__name__}: {exc}"
    return "gpu:0", "Using gpu:0 from EXTRACT_PADDLEOCR_USE_GPU."


def _configure_cache() -> None:
    cache_dir = Path(__file__).resolve().parents[3] / ".cache" / "paddlex"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(cache_dir))
    os.environ.setdefault("PADDLEOCR_HOME", str(cache_dir / "paddleocr"))
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_use_onednn", "0")
    try:
        import certifi

        ca_bundle = certifi.where()
        os.environ.setdefault("SSL_CERT_FILE", ca_bundle)
        os.environ.setdefault("CURL_CA_BUNDLE", ca_bundle)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)
    except ImportError:
        pass


def _configure_ssl_context() -> None:
    try:
        import certifi
    except ImportError:
        return
    if getattr(ssl, "_paddleocr_certifi_patch", False):
        return
    original = ssl.create_default_context

    def create_default_context(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        kwargs.setdefault("cafile", certifi.where())
        return original(*args, **kwargs)

    ssl.create_default_context = create_default_context
    setattr(ssl, "_paddleocr_certifi_patch", True)


def _run_ocr(ocr: Any, image_path: Path) -> Any:
    if hasattr(ocr, "predict"):
        return ocr.predict(str(image_path))
    if hasattr(ocr, "ocr"):
        return ocr.ocr(str(image_path))
    raise RuntimeError("Installed paddleocr package exposes neither ocr() nor predict().")


def _page_images(
    input_path: Path,
    input_type: str,
    tmp_dir: Path,
    max_pages: int,
) -> Iterator[tuple[int, Path, int]]:
    if input_type == "image":
        yield 1, input_path, 1
        return
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Install pymupdf to run local PaddleOCR on PDF pages.") from exc

    with fitz.open(str(input_path)) as document:
        page_count = document.page_count
        limit = _page_limit(page_count, max_pages)
        if limit < page_count:
            logger.info(
                "[paddle-ocr] limiting PDF OCR input=%s source_pages=%s processed_pages=%s",
                input_path.name,
                page_count,
                limit,
            )
        for page_index in range(limit):
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(settings.paddleocr_pdf_zoom, settings.paddleocr_pdf_zoom), alpha=False)
            out_path = tmp_dir / f"page-{page_index}.png"
            pixmap.save(str(out_path))
            yield page_index + 1, out_path, page_count


def _page_limit(page_count: int, max_pages: int) -> int:
    if max_pages <= 0:
        return page_count
    return max(1, min(page_count, max_pages))


def _extract_lines(raw: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if value is None:
            return
        if hasattr(value, "to_dict") and callable(value.to_dict):
            visit(value.to_dict())
            return
        if hasattr(value, "json"):
            json_value = value.json() if callable(value.json) else value.json
            visit(json_value)
            return
        if isinstance(value, dict):
            _lines_from_dict(value, lines)
            for item in value.values():
                if isinstance(item, (list, tuple, dict)):
                    visit(item)
            return
        if not isinstance(value, (list, tuple)):
            return
        if _looks_like_line(value):
            line = _line_from_sequence(value)
            if line:
                lines.append(line)
            return
        for item in value:
            visit(item)

    visit(raw)
    return _dedupe_lines(lines)


def _lines_from_dict(value: dict[str, Any], lines: list[dict[str, Any]]) -> None:
    rec_texts = value.get("rec_texts") or value.get("texts")
    rec_scores = value.get("rec_scores") or value.get("scores")
    rec_polys = value.get("rec_polys") or value.get("dt_polys") or value.get("boxes")
    if isinstance(rec_texts, list):
        for index, text in enumerate(rec_texts):
            bbox = _bbox_from_points(_index(rec_polys, index))
            confidence = _safe_confidence(_index(rec_scores, index))
            if str(text).strip():
                lines.append({"text": str(text).strip(), "bbox": bbox, "confidence": confidence})


def _looks_like_line(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], (list, tuple))
        and isinstance(value[1], (list, tuple))
        and len(value[1]) >= 1
    )


def _line_from_sequence(value: Any) -> dict[str, Any] | None:
    bbox = _bbox_from_points(value[0])
    text = str(value[1][0] if isinstance(value[1], (list, tuple)) else value[1]).strip()
    confidence = _safe_confidence(value[1][1] if isinstance(value[1], (list, tuple)) and len(value[1]) > 1 else None)
    if not text:
        return None
    return {"text": text, "bbox": bbox, "confidence": confidence}


def _lines_to_blocks(lines: list[dict[str, Any]], page_number: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, line in enumerate(sorted(lines, key=_line_sort_key)):
        text = str(line["text"]).strip()
        block_type = "table" if _looks_tabular(text) else "text"
        block = make_block(
            LIBRARY_ID,
            page_number,
            block_type,
            text,
            bbox=line.get("bbox"),
            provenance={
                "source": "paddleocr.line",
                "reading_order": index,
                "reconstruction": "line-level OCR normalized into parser block",
            },
            confidence=line.get("confidence"),
        )
        if block:
            output.append(block)
    return output


def _bbox_from_points(points: Any) -> dict[str, float] | None:
    if not isinstance(points, (list, tuple)) or not points:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for point in points:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            x = _safe_float(point[0])
            y = _safe_float(point[1])
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
    if not xs or not ys:
        return None
    return bbox_from_values(min(xs), min(ys), max(xs), max(ys))


def _line_sort_key(line: dict[str, Any]) -> tuple[float, float]:
    bbox = line.get("bbox")
    if isinstance(bbox, dict):
        return float(bbox.get("top") or 0), float(bbox.get("x0") or 0)
    return 0.0, 0.0


def _dedupe_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    output: list[dict[str, Any]] = []
    for line in lines:
        bbox = line.get("bbox") or {}
        key = (str(line.get("text") or ""), str(bbox))
        if key in seen:
            continue
        seen.add(key)
        output.append(line)
    return output


def _looks_tabular(text: str) -> bool:
    return text.count("|") >= 2 or text.count("\t") >= 2


def _avg_confidence(lines: list[dict[str, Any]]) -> float | None:
    values = [line.get("confidence") for line in lines if isinstance(line.get("confidence"), (int, float))]
    if not values:
        return None
    return round(sum(float(value) for value in values) / len(values), 4)


def _index(value: Any, index: int) -> Any:
    if isinstance(value, (list, tuple)) and index < len(value):
        return value[index]
    return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_confidence(value: Any) -> float | None:
    parsed = _safe_float(value)
    if parsed is None or parsed < 0 or parsed > 1:
        return None
    return parsed
