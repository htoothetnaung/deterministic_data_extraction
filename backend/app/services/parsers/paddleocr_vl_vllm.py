"""PaddleOCR-VL parser using a vLLM OpenAI-compatible server."""
from __future__ import annotations

import os
import ssl
import tempfile
import time
import json
import urllib.error
import urllib.request
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote
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
    skipped_result,
    structured_preview_from_blocks,
)

LIBRARY_ID = "paddleocr_vl_vllm"
DISPLAY_NAME = "PaddleOCR-VL vLLM"
SUPPORTED_INPUT_TYPES = ["pdf", "image"]


def is_available() -> bool:
    return module_available("paddleocr") and _server_available()


def availability_notes() -> str | None:
    missing: list[str] = []
    if not module_available("paddleocr"):
        missing.append("install paddleocr[doc-parser]")
    if not _server_available():
        missing.append(f"start the vLLM PaddleOCR-VL server at {settings.paddleocr_vl_vllm_server_url}")
    if missing:
        return "PaddleOCR-VL vLLM disabled: " + ", ".join(missing) + "."
    return f"Uses PaddleOCR-VL 1.6 through a vLLM server model named {_resolved_model_name()}."


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    input_type = input_type_for(input_path)
    if input_type not in SUPPORTED_INPUT_TYPES:
        return skipped_result(LIBRARY_ID, input_path, "PaddleOCR-VL vLLM supports PDF and image inputs.", preview_chars=preview_chars)
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, availability_notes() or "PaddleOCR-VL vLLM is unavailable.", preview_chars=preview_chars)

    _configure_runtime()
    from paddleocr import PaddleOCRVL

    model_name = _resolved_model_name()
    pipeline_kwargs: dict[str, Any] = {
        "pipeline_version": "v1.6",
        "vl_rec_backend": "vllm-server",
        "vl_rec_server_url": settings.paddleocr_vl_vllm_server_url,
        "vl_rec_api_model_name": model_name,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_layout_detection": True,
        "use_chart_recognition": True,
        "use_ocr_for_image_block": True,
        "format_block_content": True,
        "merge_layout_blocks": True,
        "device": settings.paddleocr_device or "gpu:0",
    }
    if settings.paddleocr_vl_vllm_api_key:
        pipeline_kwargs["vl_rec_api_key"] = settings.paddleocr_vl_vllm_api_key

    pipeline = PaddleOCRVL(**pipeline_kwargs)

    blocks: list[dict[str, Any]] = []
    markdown_parts: list[str] = []
    page_summaries: list[dict[str, Any]] = []
    markdown_image_count = 0
    source_pages = 1
    media_dir = _new_media_dir()

    with tempfile.TemporaryDirectory(prefix="paddleocr-vl-vllm-pages-") as tmp:
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
                max_pixels=settings.paddleocr_vl_vllm_max_pixels,
                max_new_tokens=settings.paddleocr_vl_vllm_max_new_tokens,
                temperature=0,
            )
            page_blocks = 0
            for result in outputs:
                markdown = _result_markdown(result)
                markdown_images = _result_markdown_images(result)
                saved_images = _save_markdown_images(markdown_images, media_dir, page_number)
                markdown = _embed_markdown_images(markdown, saved_images, page_number)
                markdown_image_count += len(saved_images)
                if markdown:
                    markdown_parts.append(f"<!-- page: {page_number} -->\n\n{markdown}")
                extracted = _blocks_from_result(result, page_number, model_name, saved_images)
                if extracted:
                    blocks.extend(extracted)
                    page_blocks += len(extracted)
                elif markdown:
                    block = make_block(
                        LIBRARY_ID,
                        page_number,
                        "markdown",
                        markdown,
                        provenance={
                            "source": "PaddleOCRVL.vllm.markdown",
                            "backend": "vllm-server",
                            "server_url": settings.paddleocr_vl_vllm_server_url,
                            "model_name": model_name,
                            "markdown_images": saved_images or _image_refs(markdown_images),
                        },
                    )
                    if not block:
                        continue
                    blocks.append(block)
                    page_blocks += 1
            page_summaries.append({"page": page_number, "image_path": str(image_path), "blocks": page_blocks})

    ordered_blocks = _layout_group_blocks(_dedupe_blocks(blocks))
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
                "backend": "vllm-server",
                "server_url": settings.paddleocr_vl_vllm_server_url,
                "model_name": model_name,
                "source_pages": source_pages,
                "processed_pages": processed_pages,
                "max_pages": settings.paddleocr_vl_vllm_max_pages,
                "truncated": input_type == "pdf" and processed_pages < source_pages,
                "max_pixels": settings.paddleocr_vl_vllm_max_pixels,
                "max_new_tokens": settings.paddleocr_vl_vllm_max_new_tokens,
                "markdown_image_count": markdown_image_count,
                "markdown_image_mode": "embedded_markdown_links",
                "media_dir": str(media_dir),
                "page_summaries": page_summaries,
                "note": "PaddleOCR-VL vLLM parser uses the full PaddleOCR document parsing pipeline with vLLM as the VL recognition backend.",
            },
        ),
        preview_chars=preview_chars,
    )


def _server_available() -> bool:
    url = settings.paddleocr_vl_vllm_server_url.rstrip("/") + "/models"
    request = urllib.request.Request(url, headers=_auth_headers())
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return 200 <= response.status < 500
    except (OSError, urllib.error.URLError):
        return False


def _served_model_ids() -> list[str]:
    url = settings.paddleocr_vl_vllm_server_url.rstrip("/") + "/models"
    request = urllib.request.Request(url, headers=_auth_headers())
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return []
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for model in models:
        if isinstance(model, dict) and model.get("id"):
            ids.append(str(model["id"]))
    return ids


def _resolved_model_name() -> str:
    configured = settings.paddleocr_vl_vllm_model_name.strip()
    served_ids = _served_model_ids()
    if configured and (not served_ids or configured in served_ids):
        return configured
    return served_ids[0] if served_ids else configured


def _auth_headers() -> dict[str, str]:
    token = settings.paddleocr_vl_vllm_api_key.strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


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
    if getattr(ssl, "_paddleocr_vl_vllm_certifi_patch", False):
        return
    original = ssl.create_default_context

    def create_default_context(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        kwargs.setdefault("cafile", ca_bundle)
        return original(*args, **kwargs)

    ssl.create_default_context = create_default_context
    setattr(ssl, "_paddleocr_vl_vllm_certifi_patch", True)


def _page_inputs(input_path: Path, input_type: str, tmp_dir: Path) -> Iterator[tuple[int, Path, int]]:
    if input_type == "image":
        yield 1, input_path, 1
        return
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Install pymupdf to run PaddleOCR-VL vLLM on PDF pages.") from exc
    with fitz.open(str(input_path)) as document:
        page_count = document.page_count
        max_pages = settings.paddleocr_vl_vllm_max_pages
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


def _new_media_dir() -> Path:
    media_dir = Path(__file__).resolve().parents[3] / "parser_outputs" / "media" / LIBRARY_ID / uuid.uuid4().hex[:12]
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir


def _save_markdown_images(images: dict[str, Any], media_dir: Path, page_number: int) -> list[dict[str, str]]:
    saved: list[dict[str, str]] = []
    for index, (key, value) in enumerate(images.items(), start=1):
        suffix = Path(str(key)).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".png"
        filename = f"page-{page_number:03d}-image-{index:02d}{suffix}"
        out_path = media_dir / filename
        if not _write_image_value(value, out_path):
            continue
        relative = out_path.relative_to(Path(__file__).resolve().parents[3] / "parser_outputs" / "media").as_posix()
        saved.append(
            {
                "key": str(key),
                "path": str(out_path),
                "url": f"/api/parser-benchmarks/media/{quote(relative)}",
                "description": f"Visual region extracted by PaddleOCR-VL on page {page_number}.",
            }
        )
    return saved


def _write_image_value(value: Any, out_path: Path) -> bool:
    try:
        if hasattr(value, "save"):
            value.save(out_path)
            return True
        if isinstance(value, (bytes, bytearray)):
            out_path.write_bytes(bytes(value))
            return True
        if isinstance(value, str):
            source = Path(value)
            if source.exists() and source.is_file():
                shutil.copyfile(source, out_path)
                return True
    except Exception:
        return False
    return False


def _embed_markdown_images(markdown: str, images: list[dict[str, str]], page_number: int) -> str:
    if not markdown or not images:
        return markdown
    image_by_key = {image["key"].replace("\\", "/"): image for image in images}

    def replace_img(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = re.search(r"""src=["']([^"']+)["']""", tag, flags=re.IGNORECASE)
        src = (src_match.group(1) if src_match else "").replace("\\", "/")
        image = image_by_key.get(src) or image_by_key.get(src.lstrip("./"))
        if not image:
            return tag
        description = _describe_visual_image(image["key"], page_number)
        image["description"] = description
        return f"![{description}]({image['url']})\n\n*Image description: {description}*"

    converted = re.sub(r"<img\b[^>]*>", replace_img, markdown, flags=re.IGNORECASE)
    converted = _strip_centering_divs(converted)
    converted = re.sub(r"\n{3,}", "\n\n", converted)
    return converted.strip()


def _strip_centering_divs(markdown: str) -> str:
    text = re.sub(r'<div[^>]*style=["\'][^"\']*text-align:\s*center;?[^"\']*["\'][^>]*>', "\n\n", markdown, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n\n", text, flags=re.IGNORECASE)
    return text


def _describe_visual_image(key: str, page_number: int) -> str:
    box = re.search(r"image_box_(\d+)_(\d+)_(\d+)_(\d+)", key)
    if not box:
        return f"Visual region extracted from page {page_number}"
    x0, y0, x1, y1 = (int(value) for value in box.groups())
    width = x1 - x0
    height = y1 - y0
    if width > 700 and height > 500:
        return f"Large visual region extracted from page {page_number}"
    if width < 120 or height < 120:
        return f"Small icon or visual marker extracted from page {page_number}"
    return f"Visual region extracted from page {page_number}"


def _blocks_from_result(result: Any, fallback_page: int, model_name: str, saved_images: list[dict[str, str]]) -> list[dict[str, Any]]:
    payload = _result_json(result)
    blocks: list[dict[str, Any]] = []
    # PaddleOCRVL.predict() is called with one rendered page image at a time.
    # Its payload page_index is therefore local to that single image and is
    # usually 0 for every PDF page. The renderer's fallback_page is the real
    # document page number.
    page_number = fallback_page
    for index, item in enumerate(payload.get("parsing_res_list") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("block_label") or "block").lower()
        content = str(item.get("block_content") or "").strip()
        block_type = "table" if "table" in label else "image" if any(token in label for token in ("image", "figure", "chart")) else "text"
        if block_type == "image" or "<img" in content.lower():
            content = _embed_markdown_images(content, saved_images, page_number or fallback_page)
        block = make_block(
            LIBRARY_ID,
            page_number or fallback_page,
            block_type,
            content or f"[{label}]",
            bbox=_bbox_from_value(item.get("block_bbox")),
            provenance={
                "source": "PaddleOCRVL.vllm.parsing_res_list",
                "backend": "vllm-server",
                "model_name": model_name,
                "block_label": item.get("block_label"),
                "block_id": item.get("block_id", index),
                "block_order": item.get("block_order"),
                "group_id": item.get("group_id"),
            },
        )
        if block:
            block["id"] = f"{block['id']}-{index}"
            blocks.append(block)
    return blocks


def _dedupe_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    seen_exact: set[tuple[int, str, str]] = set()
    for block in blocks:
        page = int(block.get("page") or 1)
        block_type = str(block.get("type") or "text").lower()
        text_key = _text_key(str(block.get("text") or ""))
        if not text_key:
            continue
        exact_key = (page, block_type, text_key)
        if exact_key in seen_exact:
            continue
        if any(_is_duplicate_block(block, existing) for existing in kept):
            continue
        seen_exact.add(exact_key)
        kept.append(block)
    return kept


def _layout_group_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    for page in sorted({int(block.get("page") or 1) for block in blocks}):
        page_blocks = [block for block in blocks if int(block.get("page") or 1) == page]
        grouped.extend(_layout_group_page(page_blocks))
    return grouped


def _layout_group_page(page_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with_bbox = [block for block in page_blocks if isinstance(block.get("bbox"), dict)]
    if len(with_bbox) < 4:
        return column_aware_blocks(page_blocks)

    page_width = max(float(block["bbox"]["x1"]) for block in with_bbox)
    text_blocks = [
        block
        for block in page_blocks
        if str(block.get("type") or "").lower() in {"text", "heading", "title"}
        and isinstance(block.get("bbox"), dict)
        and _text_key(str(block.get("text") or ""))
    ]
    passthrough = [block for block in page_blocks if block not in text_blocks]
    wide_text: list[dict[str, Any]] = []
    column_text: list[dict[str, Any]] = []
    for block in text_blocks:
        bbox = block["bbox"]
        width = float(bbox["x1"]) - float(bbox["x0"])
        if width >= page_width * 0.58:
            wide_text.append(block)
        else:
            column_text.append(block)

    columns = _column_clusters(column_text, page_width)
    grouped_text: list[dict[str, Any]] = []
    for column_index, column in enumerate(columns):
        grouped_text.extend(_group_column_blocks(column, column_index))

    return sorted([*passthrough, *wide_text, *grouped_text], key=_layout_sort_key)


def _column_clusters(blocks: list[dict[str, Any]], page_width: float) -> list[list[dict[str, Any]]]:
    clusters: list[dict[str, Any]] = []
    tolerance = max(55.0, page_width * 0.045)
    for block in sorted(blocks, key=lambda item: (float(item["bbox"]["x0"]), float(item["bbox"]["top"]))):
        x0 = float(block["bbox"]["x0"])
        matched: dict[str, Any] | None = None
        for cluster in clusters:
            if abs(x0 - float(cluster["x0"])) <= tolerance:
                matched = cluster
                break
        if matched is None:
            clusters.append({"x0": x0, "blocks": [block]})
        else:
            matched["blocks"].append(block)
            matched["x0"] = min(float(matched["x0"]), x0)
    return [sorted(cluster["blocks"], key=_raw_block_sort_key) for cluster in sorted(clusters, key=lambda item: float(item["x0"]))]


def _group_column_blocks(blocks: list[dict[str, Any]], column_index: int) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for block in blocks:
        if previous is None:
            current = [block]
            previous = block
            continue
        if _starts_new_group(previous, block):
            groups.append(current)
            current = [block]
        else:
            current.append(block)
        previous = block
    if current:
        groups.append(current)

    output: list[dict[str, Any]] = []
    for group_index, group in enumerate(groups):
        if len(group) == 1:
            output.append(group[0])
            continue
        merged = _merge_text_group(group, column_index, group_index)
        if merged:
            output.append(merged)
    return output


def _starts_new_group(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    previous_bbox = previous["bbox"]
    current_bbox = current["bbox"]
    gap = float(current_bbox["top"]) - float(previous_bbox["bottom"])
    current_text = str(current.get("text") or "").strip()
    previous_text = str(previous.get("text") or "").strip()
    if gap > 62:
        return True
    if _looks_like_section_heading(current_text) and not _looks_like_section_heading(previous_text):
        return True
    return False


def _merge_text_group(group: list[dict[str, Any]], column_index: int, group_index: int) -> dict[str, Any] | None:
    ordered = sorted(group, key=_raw_block_sort_key)
    text_parts = [str(block.get("text") or "").strip() for block in ordered if str(block.get("text") or "").strip()]
    text = _join_group_text(text_parts)
    if not text:
        return None
    bbox = _union_bbox([block["bbox"] for block in ordered if isinstance(block.get("bbox"), dict)])
    first = ordered[0]
    block_type = "heading" if len(ordered) == 1 and _looks_like_section_heading(text) else "text"
    block = make_block(
        LIBRARY_ID,
        int(first.get("page") or 1),
        block_type,
        text,
        bbox=bbox,
        provenance={
            "source": "PaddleOCRVL.vllm.layout_group",
            "column": column_index,
            "group_index": group_index,
            "child_block_count": len(ordered),
            "child_ids": [str(block.get("id")) for block in ordered[:20]],
        },
    )
    return block


def _join_group_text(parts: list[str]) -> str:
    lines: list[str] = []
    for part in parts:
        clean = re.sub(r"\s+", " ", part).strip()
        if not clean:
            continue
        if lines and clean.lower() == lines[-1].lower():
            continue
        if _looks_like_section_heading(clean):
            lines.append(clean)
        elif lines and not lines[-1].endswith((".", ":", ";", "!", "?")) and not _looks_like_section_heading(lines[-1]):
            lines[-1] = f"{lines[-1]} {clean}"
        else:
            lines.append(clean)
    return "\n\n".join(lines)


def _looks_like_section_heading(text: str) -> bool:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean or len(clean) > 90:
        return False
    alpha = re.sub(r"[^A-Za-z]", "", clean)
    if len(alpha) < 3:
        return False
    uppercase_ratio = sum(1 for char in alpha if char.isupper()) / len(alpha)
    return uppercase_ratio >= 0.72


def _union_bbox(boxes: list[dict[str, Any]]) -> dict[str, float] | None:
    if not boxes:
        return None
    return bbox_from_values(
        min(float(box["x0"]) for box in boxes),
        min(float(box["top"]) for box in boxes),
        max(float(box["x1"]) for box in boxes),
        max(float(box["bottom"]) for box in boxes),
    )


def _layout_sort_key(block: dict[str, Any]) -> tuple[int, float, float]:
    bbox = block.get("bbox")
    if not isinstance(bbox, dict):
        return (1, 0.0, 0.0)
    width = float(bbox["x1"]) - float(bbox["x0"])
    top = float(bbox["top"])
    left = float(bbox["x0"])
    wide_rank = 0 if width > 650 and top < 140 else 1
    return (wide_rank, left, top)


def _raw_block_sort_key(block: dict[str, Any]) -> tuple[float, float]:
    bbox = block.get("bbox")
    if isinstance(bbox, dict):
        return float(bbox.get("top") or 0), float(bbox.get("x0") or 0)
    return 0.0, 0.0


def _is_duplicate_block(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    if int(candidate.get("page") or 1) != int(existing.get("page") or 1):
        return False
    if str(candidate.get("type") or "").lower() != str(existing.get("type") or "").lower():
        return False
    candidate_text = _text_key(str(candidate.get("text") or ""))
    existing_text = _text_key(str(existing.get("text") or ""))
    if not candidate_text or candidate_text != existing_text:
        return False
    candidate_bbox = candidate.get("bbox")
    existing_bbox = existing.get("bbox")
    if not isinstance(candidate_bbox, dict) or not isinstance(existing_bbox, dict):
        return True
    return _bbox_iou(candidate_bbox, existing_bbox) >= 0.5


def _text_key(text: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"!\[[^\]]*]\([^)]+\)", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip().lower()
    return clean[:500]


def _bbox_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    try:
        ax0, ay0, ax1, ay1 = float(a["x0"]), float(a["top"]), float(a["x1"]), float(a["bottom"])
        bx0, by0, bx1, by1 = float(b["x0"]), float(b["top"]), float(b["x1"]), float(b["bottom"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    inter_width = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    inter_height = max(0.0, min(ay1, by1) - max(ay0, by0))
    intersection = inter_width * inter_height
    if intersection <= 0:
        return 0.0
    a_area = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    b_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = a_area + b_area - intersection
    return intersection / union if union > 0 else 0.0


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
