"""Local PaddleOCR-VL parser using GGUF weights through llama.cpp server."""
from __future__ import annotations

import os
import ssl
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

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
    preview_text,
    project_root,
    skipped_result,
    structured_preview_from_blocks,
)

LIBRARY_ID = "paddleocr_vl_local"
DISPLAY_NAME = "PaddleOCR-VL local GGUF"
SUPPORTED_INPUT_TYPES = ["pdf", "image"]


def is_available() -> bool:
    return (
        module_available("paddleocr")
        and _model_path().exists()
        and _mmproj_path().exists()
        and _server_available()
    )


def availability_notes() -> str | None:
    missing: list[str] = []
    if not module_available("paddleocr"):
        missing.append("install paddleocr[doc-parser]")
    if not _model_path().exists():
        missing.append("set EXTRACT_PADDLEOCR_VL_LOCAL_MODEL_PATH to PaddleOCR-VL-1.6-GGUF.gguf")
    if not _mmproj_path().exists():
        missing.append("set EXTRACT_PADDLEOCR_VL_LOCAL_MMPROJ_PATH to PaddleOCR-VL-1.6-GGUF-mmproj.gguf")
    if not _server_available():
        missing.append(f"start llama-server at {settings.paddleocr_vl_local_server_url}")
    if missing:
        return "PaddleOCR-VL local disabled: " + ", ".join(missing) + "."
    return "Uses local PaddleOCR-VL 1.6 GGUF through llama.cpp server; PDF pages are processed sequentially."


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    input_type = input_type_for(input_path)
    if input_type not in SUPPORTED_INPUT_TYPES:
        return skipped_result(LIBRARY_ID, input_path, "PaddleOCR-VL local supports PDF and image inputs.", preview_chars=preview_chars)
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, availability_notes() or "PaddleOCR-VL local is unavailable.", preview_chars=preview_chars)

    _configure_runtime()
    from paddleocr import PaddleOCRVL

    pipeline = PaddleOCRVL(
        pipeline_version="v1.6",
        vl_rec_backend="llama-cpp-server",
        vl_rec_server_url=settings.paddleocr_vl_local_server_url,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_layout_detection=True,
        use_chart_recognition=True,
        use_ocr_for_image_block=True,
        format_block_content=True,
        merge_layout_blocks=True,
        device=settings.paddleocr_device or "gpu:0",
    )

    blocks: list[dict[str, Any]] = []
    markdown_parts: list[str] = []
    page_summaries: list[dict[str, Any]] = []
    markdown_image_count = 0
    source_pages = 1

    with tempfile.TemporaryDirectory(prefix="paddleocr-vl-local-pages-") as tmp:
        for page_number, image_path, source_pages in _page_inputs(input_path, input_type, Path(tmp)):
            outputs = pipeline.predict(
                str(image_path),
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_layout_detection=True,
                use_chart_recognition=True,
                use_ocr_for_image_block=True,
                format_block_content=True,
                merge_layout_blocks=True,
                max_pixels=settings.paddleocr_vl_local_max_pixels,
                max_new_tokens=settings.paddleocr_vl_local_max_new_tokens,
                temperature=0,
            )
            page_blocks = 0
            for result in outputs:
                markdown = _result_markdown(result)
                if markdown:
                    markdown_parts.append(f"<!-- page: {page_number} -->\n\n{markdown}")
                markdown_images = _result_markdown_images(result)
                markdown_image_count += len(markdown_images)
                block = make_block(
                    LIBRARY_ID,
                    page_number,
                    "markdown",
                    markdown,
                    provenance={
                        "source": "PaddleOCRVL.local.markdown",
                        "backend": "llama-cpp-server",
                        "server_url": settings.paddleocr_vl_local_server_url,
                        "markdown_images": _image_refs(markdown_images),
                    },
                )
                if block:
                    blocks.append(block)
                    page_blocks += 1
                extracted = _blocks_from_result(result, page_number)
                blocks.extend(extracted)
                page_blocks += len(extracted)
            page_summaries.append({"page": page_number, "image_path": str(image_path), "blocks": page_blocks})

    ordered_blocks = column_aware_blocks(blocks)
    text = blocks_to_markdown(ordered_blocks) or "\n\n".join(markdown_parts)
    processed_pages = max((summary["page"] for summary in page_summaries), default=1)
    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=processed_pages,
        tables=sum(1 for block in ordered_blocks if block.get("type") == "table"),
        images=sum(1 for block in ordered_blocks if block.get("type") == "image") + markdown_image_count,
        structured_preview=structured_preview_from_blocks(
            ordered_blocks,
            text,
            preview_chars,
            {
                "backend": "llama-cpp-server",
                "server_url": settings.paddleocr_vl_local_server_url,
                "model_path": str(_model_path()),
                "mmproj_path": str(_mmproj_path()),
                "source_pages": source_pages,
                "processed_pages": processed_pages,
                "max_pages": settings.paddleocr_vl_local_max_pages,
                "truncated": input_type == "pdf" and processed_pages < source_pages,
                "max_pixels": settings.paddleocr_vl_local_max_pixels,
                "max_new_tokens": settings.paddleocr_vl_local_max_new_tokens,
                "markdown_image_count": markdown_image_count,
                "page_summaries": page_summaries,
                "note": "Local PaddleOCR-VL 1.6 uses GGUF weights through a running llama.cpp server; pages are processed sequentially to protect laptop VRAM.",
            },
        ),
        preview_chars=preview_chars,
    )


def _model_path() -> Path:
    configured = str(settings.paddleocr_vl_local_model_path or "").strip()
    if configured:
        return _resolve_path(configured)
    return project_root() / "model_weights" / "PaddleOCR-VL-1.6-GGUF" / "PaddleOCR-VL-1.6-GGUF.gguf"


def _mmproj_path() -> Path:
    configured = str(settings.paddleocr_vl_local_mmproj_path or "").strip()
    if configured:
        return _resolve_path(configured)
    return project_root() / "model_weights" / "PaddleOCR-VL-1.6-GGUF" / "PaddleOCR-VL-1.6-GGUF-mmproj.gguf"


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_root() / path


def _server_available() -> bool:
    url = settings.paddleocr_vl_local_server_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return 200 <= response.status < 500
    except (OSError, urllib.error.URLError):
        return False


def _configure_runtime() -> None:
    cache_dir = Path(__file__).resolve().parents[3] / ".cache" / "paddlex"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(cache_dir))
    os.environ.setdefault("PADDLEOCR_HOME", str(cache_dir / "paddleocr"))
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    try:
        import certifi
    except ImportError:
        return
    ca_bundle = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", ca_bundle)
    os.environ.setdefault("CURL_CA_BUNDLE", ca_bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)
    if getattr(ssl, "_paddleocr_vl_local_certifi_patch", False):
        return
    original = ssl.create_default_context

    def create_default_context(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        kwargs.setdefault("cafile", ca_bundle)
        return original(*args, **kwargs)

    ssl.create_default_context = create_default_context
    setattr(ssl, "_paddleocr_vl_local_certifi_patch", True)


def _page_inputs(input_path: Path, input_type: str, tmp_dir: Path) -> Iterator[tuple[int, Path, int]]:
    if input_type == "image":
        yield 1, input_path, 1
        return
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Install pymupdf to run local PaddleOCR-VL on PDF pages.") from exc
    with fitz.open(str(input_path)) as document:
        page_count = document.page_count
        max_pages = settings.paddleocr_vl_local_max_pages
        limit = page_count if max_pages <= 0 else max(1, min(page_count, max_pages))
        for page_index in range(limit):
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(settings.paddleocr_pdf_zoom, settings.paddleocr_pdf_zoom), alpha=False)
            out_path = tmp_dir / f"page-{page_index + 1}.png"
            pixmap.save(str(out_path))
            yield page_index + 1, out_path, page_count


def _result_markdown(result: Any) -> str:
    try:
        markdown = result.markdown
    except Exception:
        return ""
    if isinstance(markdown, dict):
        return str(markdown.get("markdown_texts") or markdown.get("markdown_text") or "").strip()
    return str(markdown or "").strip()


def _result_markdown_images(result: Any) -> dict[str, Any]:
    try:
        markdown = result.markdown
    except Exception:
        return {}
    if isinstance(markdown, dict) and isinstance(markdown.get("markdown_images"), dict):
        return markdown["markdown_images"]
    return {}


def _blocks_from_result(result: Any, fallback_page: int) -> list[dict[str, Any]]:
    payload = _result_json(result)
    blocks: list[dict[str, Any]] = []
    page_number = int(payload.get("page_index", fallback_page - 1) or 0) + 1
    for index, item in enumerate(payload.get("parsing_res_list") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("block_label") or "block").lower()
        content = str(item.get("block_content") or "").strip()
        block_type = "table" if "table" in label else "image" if any(token in label for token in ("image", "figure", "chart")) else "text"
        block = make_block(
            LIBRARY_ID,
            page_number or fallback_page,
            block_type,
            content or f"[{label}]",
            bbox=_bbox_from_value(item.get("block_bbox")),
            provenance={
                "source": "PaddleOCRVL.local.parsing_res_list",
                "backend": "llama-cpp-server",
                "block_label": item.get("block_label"),
                "block_id": item.get("block_id", index),
                "block_order": item.get("block_order"),
                "group_id": item.get("group_id"),
            },
        )
        if block:
            blocks.append(block)
    return blocks


def _result_json(result: Any) -> dict[str, Any]:
    try:
        payload = result.json
    except Exception:
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("res"), dict):
        return payload["res"]
    return payload if isinstance(payload, dict) else {}


def _bbox_from_value(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict):
        return bbox_from_values(value.get("x0"), value.get("top", value.get("y0")), value.get("x1"), value.get("bottom", value.get("y1")))
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        if all(isinstance(item, (list, tuple)) for item in value):
            xs = [float(point[0]) for point in value if len(point) >= 2]
            ys = [float(point[1]) for point in value if len(point) >= 2]
            return bbox_from_values(min(xs), min(ys), max(xs), max(ys)) if xs and ys else None
        return bbox_from_values(value[0], value[1], value[2], value[3])
    return None


def _image_refs(images: dict[str, Any]) -> list[dict[str, str]]:
    return [{"key": str(key), "kind": type(value).__name__, "preview": preview_text(str(value), 240)} for key, value in images.items()]
