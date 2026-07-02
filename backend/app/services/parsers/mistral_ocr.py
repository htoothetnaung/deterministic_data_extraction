"""Mistral OCR implementation using the documented REST API."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote
import uuid
from typing import Any

from app.core.config import settings
from app.models.parser_benchmark import ParserRunResult
from app.services.parsers.base import (
    bbox_from_values,
    input_type_for,
    make_block,
    ok_result,
    preview_text,
    project_root,
    skipped_result,
)

LIBRARY_ID = "mistral_ocr"
DISPLAY_NAME = "Mistral OCR"
SUPPORTED_INPUT_TYPES = ["pdf", "image", "document"]

OCR_ENDPOINT = "https://api.mistral.ai/v1/ocr"


def is_available() -> bool:
    return bool(_api_key())


def availability_notes() -> str | None:
    if is_available():
        return "Uses Mistral Document AI OCR over HTTPS."
    return "Set MISTRAL_API_KEY or EXTRACT_MISTRAL_API_KEY to enable this parser."


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    input_type = input_type_for(input_path)
    if input_type not in SUPPORTED_INPUT_TYPES:
        return skipped_result(
            LIBRARY_ID,
            input_path,
            "Mistral OCR supports PDF, image, and document inputs in this adapter.",
            preview_chars=preview_chars,
        )

    api_key = _api_key()
    if not api_key:
        return skipped_result(
            LIBRARY_ID,
            input_path,
            "Set MISTRAL_API_KEY or EXTRACT_MISTRAL_API_KEY to enable Mistral OCR.",
            preview_chars=preview_chars,
        )

    max_bytes = max(settings.mistral_ocr_max_inline_mb, 1) * 1024 * 1024
    file_size = input_path.stat().st_size
    if file_size > max_bytes:
        return skipped_result(
            LIBRARY_ID,
            input_path,
            (
                f"{input_path.name} is {file_size / (1024 * 1024):.1f} MB, which is above the "
                f"configured inline base64 limit of {settings.mistral_ocr_max_inline_mb} MB. "
                "Use Mistral's uploaded-file signed URL flow for larger documents."
            ),
            preview_chars=preview_chars,
        )

    response = _process_ocr(api_key, input_path, input_type)
    pages = response.get("pages") or []
    media_dir = _new_media_dir()
    markdown_parts = []
    for page in pages:
        page_number = int(page.get("index", 0)) + 1
        markdown = str(page.get("markdown") or "").strip()
        markdown = _embed_page_artifacts(markdown, page, media_dir, page_number)
        if markdown:
            markdown_parts.append(f"<!-- page: {page_number} -->\n\n{markdown}")

    text = "\n\n".join(markdown_parts)
    table_count = sum(len(page.get("tables") or []) for page in pages if isinstance(page, dict))
    image_count = sum(len(page.get("images") or []) for page in pages if isinstance(page, dict))

    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=len(pages),
        tables=table_count,
        images=image_count,
        structured_preview=_structured_preview(response, preview_chars, media_dir),
        preview_chars=preview_chars,
    )


def _api_key() -> str:
    return settings.mistral_api_key or os.environ.get("MISTRAL_API_KEY", "") or _env_file_api_key()


def _env_file_api_key() -> str:
    for env_path in (project_root() / ".env", project_root() / "backend" / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "MISTRAL_API_KEY":
                return value.strip().strip("\"'")
    return ""


def _process_ocr(api_key: str, input_path: Path, input_type: str) -> dict[str, Any]:
    payload = _request_payload(input_path, input_type)
    request = urllib.request.Request(
        OCR_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=settings.mistral_ocr_timeout_seconds,
            context=_ssl_context(),
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Mistral OCR HTTP {exc.code}: {body[:1000]}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"Mistral OCR connection failed: {reason}") from exc
    except ssl.SSLError as exc:
        raise RuntimeError(
            "Mistral OCR TLS setup failed. Check SSL_CERT_FILE/CURL_CA_BUNDLE or set "
            "EXTRACT_MISTRAL_OCR_CA_BUNDLE to a valid PEM certificate bundle. "
            f"OpenSSL said: {exc}"
        ) from exc


def _ssl_context() -> ssl.SSLContext:
    cafile = settings.mistral_ocr_ca_bundle.strip()
    if not cafile:
        try:
            import certifi

            cafile = certifi.where()
        except ImportError:
            cafile = ""
    return ssl.create_default_context(cafile=cafile or None)


def _request_payload(input_path: Path, input_type: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": settings.mistral_ocr_model,
        "document": _document_payload(input_path, input_type),
        "include_image_base64": settings.mistral_ocr_include_images,
    }
    optional_fields = {
        "table_format": settings.mistral_ocr_table_format,
        "confidence_scores_granularity": settings.mistral_ocr_confidence_granularity,
    }
    for key, value in optional_fields.items():
        if value:
            payload[key] = value
    if settings.mistral_ocr_extract_header:
        payload["extract_header"] = True
    if settings.mistral_ocr_extract_footer:
        payload["extract_footer"] = True
    return payload


def _document_payload(input_path: Path, input_type: str) -> dict[str, str]:
    data_url = _data_url(input_path)
    if input_type == "image":
        return {"type": "image_url", "image_url": data_url}
    return {"type": "document_url", "document_url": data_url}


def _data_url(input_path: Path) -> str:
    content_type = mimetypes.guess_type(input_path.name)[0]
    if not content_type:
        content_type = "application/octet-stream"
    encoded = base64.b64encode(input_path.read_bytes()).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _structured_preview(response: dict[str, Any], preview_chars: int, media_dir: Path | None = None) -> dict[str, Any]:
    pages = response.get("pages") or []
    blocks: list[dict[str, Any]] = []
    page_summaries: list[dict[str, Any]] = []
    table_samples: list[dict[str, Any]] = []
    image_samples: list[dict[str, Any]] = []

    for page in pages:
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("index", 0)) + 1
        tables = page.get("tables") or []
        images = page.get("images") or []
        markdown = _embed_page_artifacts(str(page.get("markdown") or ""), page, media_dir, page_number) if media_dir else str(page.get("markdown") or "")
        confidence_scores = page.get("confidence_scores") or {}
        markdown_block = make_block(
            LIBRARY_ID,
            page_number,
            "markdown",
            markdown,
            provenance={
                "source": "mistral_ocr.pages[].markdown",
                "model": response.get("model"),
                "dimensions": page.get("dimensions") or {},
                "confidence_scores": _confidence_summary(confidence_scores),
            },
            confidence=confidence_scores.get("average_page_confidence_score"),
        )
        if markdown_block:
            blocks.append(markdown_block)
        if len(page_summaries) < 50:
            page_summaries.append(
                {
                    "page": page_number,
                    "markdown_preview": preview_text(str(page.get("markdown") or ""), min(preview_chars, 1200)),
                    "tables": len(tables),
                    "images": len(images),
                    "hyperlinks": len(page.get("hyperlinks") or []),
                    "has_header": bool(page.get("header")),
                    "has_footer": bool(page.get("footer")),
                    "dimensions": page.get("dimensions") or {},
                    "average_confidence": confidence_scores.get("average_page_confidence_score"),
                    "minimum_confidence": confidence_scores.get("minimum_page_confidence_score"),
                }
            )
        for table in tables:
            if len(table_samples) >= 5:
                break
            table_samples.append(_table_sample(page_number, table))
            block = make_block(
                LIBRARY_ID,
                page_number,
                "table",
                str(table.get("content") if isinstance(table, dict) else table),
                provenance={
                    "source": "mistral_ocr.pages[].tables",
                    "table": _strip_large_fields(table),
                },
            )
            if block:
                blocks.append(block)
        for image in images:
            if len(image_samples) >= 8:
                break
            image_samples.append(_image_sample(page_number, image))
            block = make_block(
                LIBRARY_ID,
                page_number,
                "image",
                str(image.get("id") if isinstance(image, dict) else "image"),
                bbox=_mistral_image_bbox(image),
                provenance={
                    "source": "mistral_ocr.pages[].images",
                    "image": _strip_large_fields(image),
                },
            )
            if block:
                blocks.append(block)

    return {
        "model": response.get("model"),
        "usage_info": response.get("usage_info") or {},
        "table_format": settings.mistral_ocr_table_format or None,
        "confidence_scores_granularity": settings.mistral_ocr_confidence_granularity or None,
        "extract_header": settings.mistral_ocr_extract_header,
        "extract_footer": settings.mistral_ocr_extract_footer,
        "include_image_base64": settings.mistral_ocr_include_images,
        "media_dir": str(media_dir) if media_dir else None,
        "page_summaries": page_summaries,
        "blocks": blocks,
        "block_samples": [
            {
                "page": block.get("page"),
                "type": block.get("type"),
                "bbox": block.get("bbox"),
                "text_preview": block.get("text_preview"),
                "provenance": block.get("provenance"),
            }
            for block in blocks[:12]
        ],
        "table_samples": table_samples,
        "image_samples": image_samples,
    }


def _table_sample(page_number: int, table: Any) -> dict[str, Any]:
    if isinstance(table, dict):
        table_id = table.get("id")
        content = str(table.get("content") or "")
        table_format = table.get("format")
    else:
        table_id = None
        content = str(table)
        table_format = None
    return {
        "page": page_number,
        "id": table_id,
        "format": table_format,
        "rows": None,
        "columns": None,
        "sample": [[preview_text(content, 800)]],
    }


def _new_media_dir() -> Path:
    media_dir = project_root() / "backend" / "parser_outputs" / "media" / LIBRARY_ID / uuid.uuid4().hex[:12]
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir


def _embed_page_artifacts(markdown: str, page: dict[str, Any], media_dir: Path, page_number: int) -> str:
    if not markdown:
        return markdown
    output = _embed_table_links(markdown, page.get("tables") or [])
    output = _embed_image_links(output, page.get("images") or [], media_dir, page_number)
    return output


def _embed_table_links(markdown: str, tables: list[Any]) -> str:
    table_by_id: dict[str, str] = {}
    for index, table in enumerate(tables, start=1):
        if not isinstance(table, dict):
            continue
        table_id = str(table.get("id") or f"tbl-{index}.html")
        content = str(table.get("content") or "").strip()
        if content:
            table_by_id[table_id] = _normalize_table_html(content)

    def replace(match: re.Match[str]) -> str:
        label = match.group(1)
        href = match.group(2)
        table_id = href or label
        html = table_by_id.get(table_id) or table_by_id.get(label)
        return html if html else match.group(0)

    return re.sub(r"\[([^\]]+\.html)\]\(([^)]+\.html)\)", replace, markdown)


def _normalize_table_html(content: str) -> str:
    stripped = content.strip()
    if "<table" in stripped.lower():
        return stripped
    return f"<table><tbody><tr><td>{stripped}</td></tr></tbody></table>"


def _embed_image_links(markdown: str, images: list[Any], media_dir: Path, page_number: int) -> str:
    image_urls: dict[str, str] = {}
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            continue
        image_id = str(image.get("id") or f"img-{index}.png")
        image_base64 = str(image.get("image_base64") or "").strip()
        if not image_base64:
            continue
        suffix = Path(image_id).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".png"
        path = media_dir / f"page-{page_number:03d}-image-{index:02d}{suffix}"
        if not _write_base64_image(image_base64, path):
            continue
        relative = path.relative_to(project_root() / "backend" / "parser_outputs" / "media").as_posix()
        image_urls[image_id] = f"/api/parser-benchmarks/media/{quote(relative)}"

    def replace(match: re.Match[str]) -> str:
        label = match.group(1)
        href = match.group(2)
        url = image_urls.get(href) or image_urls.get(label)
        return f"![{label}]({url})" if url else match.group(0)

    return re.sub(r"\[([^\]]+\.(?:png|jpe?g|webp))\]\(([^)]+\.(?:png|jpe?g|webp))\)", replace, markdown, flags=re.IGNORECASE)


def _write_base64_image(value: str, path: Path) -> bool:
    try:
        payload = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
        path.write_bytes(base64.b64decode(payload))
        return True
    except Exception:
        return False


def _image_sample(page_number: int, image: Any) -> dict[str, Any]:
    if not isinstance(image, dict):
        return {"page": page_number, "preview": preview_text(str(image), 400)}
    return {
        "page": page_number,
        "id": image.get("id"),
        "bbox": {
            "top_left_x": image.get("top_left_x"),
            "top_left_y": image.get("top_left_y"),
            "bottom_right_x": image.get("bottom_right_x"),
            "bottom_right_y": image.get("bottom_right_y"),
        },
        "has_image_base64": bool(image.get("image_base64")),
        "image_annotation": image.get("image_annotation"),
    }


def _mistral_image_bbox(image: Any) -> dict[str, float] | None:
    if not isinstance(image, dict):
        return None
    return bbox_from_values(
        image.get("top_left_x"),
        image.get("top_left_y"),
        image.get("bottom_right_x"),
        image.get("bottom_right_y"),
    )


def _confidence_summary(confidence_scores: Any) -> dict[str, Any]:
    if not isinstance(confidence_scores, dict):
        return {}
    return {
        "average_page_confidence_score": confidence_scores.get("average_page_confidence_score"),
        "minimum_page_confidence_score": confidence_scores.get("minimum_page_confidence_score"),
        "word_confidence_count": len(confidence_scores.get("word_confidence_scores") or []),
    }


def _strip_large_fields(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        key: ("[base64 omitted]" if key == "image_base64" else item)
        for key, item in value.items()
    }
