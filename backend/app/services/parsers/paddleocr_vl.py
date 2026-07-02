"""PaddleOCR-VL API parser adapter via langchain-paddleocr."""
from __future__ import annotations

import os
import ssl
import time
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from app.core.config import settings
from app.models.parser_benchmark import ParserRunResult
from app.services.parsers.base import (
    input_type_for,
    make_block,
    module_available,
    ok_result,
    preview_text,
    skipped_result,
    structured_preview_from_blocks,
)

LIBRARY_ID = "paddleocr_vl"
DISPLAY_NAME = "PaddleOCR-VL API"
SUPPORTED_INPUT_TYPES = ["pdf", "image"]
DEFAULT_BASE_URL = "https://paddleocr.aistudio-app.com"


def is_available() -> bool:
    return module_available("langchain_paddleocr") and bool(_base_url()) and bool(_access_token())


def availability_notes() -> str | None:
    if is_available():
        return "Uses PaddleOCR-VL through langchain-paddleocr for layout-aware document parsing."
    missing: list[str] = []
    if not module_available("langchain_paddleocr"):
        missing.append("install langchain-paddleocr")
    if not _base_url():
        missing.append("set EXTRACT_PADDLEOCR_VL_API_URL or EXTRACT_PADDLEOCR_VL_BASE_URL")
    if not _access_token():
        missing.append("set AISTUDIO_ACCESS_TOKEN, EXTRACT_AISTUDIO_ACCESS_TOKEN, PADDLEOCR_ACCESS_TOKEN, or EXTRACT_PADDLEOCR_ACCESS_TOKEN")
    return "PaddleOCR-VL disabled: " + ", ".join(missing) + "."


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    input_type = input_type_for(input_path)
    if input_type not in SUPPORTED_INPUT_TYPES:
        return skipped_result(
            LIBRARY_ID,
            input_path,
            "PaddleOCR-VL supports PDF and image inputs.",
            preview_chars=preview_chars,
        )
    if not is_available():
        return skipped_result(LIBRARY_ID, input_path, availability_notes() or "PaddleOCR-VL is unavailable.", preview_chars=preview_chars)

    _configure_paddle_runtime()
    from langchain_paddleocr import PaddleOCRVLLoader

    token = _access_token()
    _publish_token_for_sdk(token)
    loader = PaddleOCRVLLoader(
        file_path=str(input_path),
        base_url=_base_url(),
        access_token=SecretStr(token),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_layout_detection=True,
        use_chart_recognition=True,
        use_ocr_for_image_block=True,
        format_block_content=True,
        merge_layout_blocks=True,
        prettify_markdown=True,
        restructure_pages=True,
        merge_tables=True,
        return_markdown_images=True,
        timeout=settings.paddleocr_vl_timeout_seconds,
    )
    docs = loader.load()
    blocks: list[dict[str, Any]] = []
    markdown_parts: list[str] = []
    raw_responses: list[Any] = []
    page_count = 0
    markdown_image_count = 0
    output_image_count = 0
    for doc_index, doc in enumerate(docs, start=1):
        raw = doc.metadata.get("paddleocr_vl_raw_response")
        raw_responses.append(raw)
        pages = _raw_pages(raw)
        if not pages:
            pages = [{"markdown_text": str(doc.page_content or "").strip()}]
        page_count += len(pages)
        for page_index, page in enumerate(pages, start=1):
            page_number = _metadata_page(doc.metadata, page_index if len(docs) == 1 else doc_index)
            if len(pages) > 1:
                page_number = page_index
            content = str(page.get("markdown_text") or "").strip()
            if content:
                markdown_parts.append(f"<!-- page: {page_number} -->\n\n{content}")
            markdown_images = _image_refs(page.get("markdown_images"))
            output_images = _image_refs(page.get("output_images"))
            markdown_image_count += len(markdown_images)
            output_image_count += len(output_images)
            block = make_block(
                LIBRARY_ID,
                page_number,
                "markdown",
                content,
                provenance={
                    "source": "PaddleOCRVLLoader.raw_response.pages.markdown_text",
                    "metadata": _metadata_preview(doc.metadata),
                    "markdown_images": markdown_images,
                    "output_images": output_images,
                },
            )
            if block:
                blocks.append(block)
            blocks.extend(_blocks_from_raw_response(page, page_number))

    text = "\n\n".join(markdown_parts)
    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=max((int(block.get("page") or 1) for block in blocks), default=max(page_count, len(docs), 1)),
        tables=sum(1 for block in blocks if str(block.get("type")) == "table"),
        images=sum(1 for block in blocks if str(block.get("type")) == "image") + markdown_image_count + output_image_count,
        structured_preview=structured_preview_from_blocks(
            blocks,
            text,
            preview_chars,
            {
                "loader": "langchain_paddleocr.PaddleOCRVLLoader",
                "base_url": _redacted_url(_base_url()),
                "auth_source": _access_token_source(),
                "raw_response_count": len([item for item in raw_responses if item is not None]),
                "markdown_image_count": markdown_image_count,
                "output_image_count": output_image_count,
                "note": "PaddleOCR-VL returns AI-ready page Markdown plus optional image references; raw API response pages are summarized into parser blocks where possible.",
            },
        ),
        preview_chars=preview_chars,
    )


def _base_url() -> str:
    return (
        settings.paddleocr_vl_base_url
        or settings.paddleocr_vl_api_url
        or _env_value("EXTRACT_PADDLEOCR_VL_BASE_URL")
        or _env_value("EXTRACT_PADDLEOCR_VL_API_URL")
        or _env_value("PADDLEOCR_VL_BASE_URL")
        or _env_value("PADDLEOCR_VL_API_URL")
        or DEFAULT_BASE_URL
    ).strip()


def _access_token() -> str:
    source, token = _access_token_with_source()
    return token


def _access_token_source() -> str:
    source, token = _access_token_with_source()
    return source if token else ""


def _access_token_with_source() -> tuple[str, str]:
    candidates = [
        ("EXTRACT_AISTUDIO_ACCESS_TOKEN", settings.aistudio_access_token),
        ("EXTRACT_PADDLEOCR_ACCESS_TOKEN", settings.paddleocr_access_token),
        ("AISTUDIO_ACCESS_TOKEN", _env_value("AISTUDIO_ACCESS_TOKEN")),
        ("PADDLEOCR_ACCESS_TOKEN", _env_value("PADDLEOCR_ACCESS_TOKEN")),
        ("AI Studio token cache", _aistudio_token_file()),
    ]
    for source, token in candidates:
        token = str(token or "").strip()
        if token:
            return source, token
    return "", ""


def _publish_token_for_sdk(token: str) -> None:
    # The official loader also reads PADDLEOCR_ACCESS_TOKEN internally. Publish
    # the resolved token so both explicit and SDK env-based auth paths agree.
    os.environ["PADDLEOCR_ACCESS_TOKEN"] = token


def _configure_paddle_runtime() -> None:
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
    if getattr(ssl, "_paddleocr_vl_certifi_patch", False):
        return
    original = ssl.create_default_context

    def create_default_context(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        kwargs.setdefault("cafile", ca_bundle)
        return original(*args, **kwargs)

    ssl.create_default_context = create_default_context
    setattr(ssl, "_paddleocr_vl_certifi_patch", True)


def _redacted_url(url: str) -> str:
    return url.split("?", 1)[0]


def _env_value(name: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    for env_file in _env_files():
        value = _read_dotenv_value(env_file, name)
        if value:
            return value
    return ""


def _env_files() -> list[Path]:
    backend_dir = Path(__file__).resolve().parents[3]
    return [backend_dir / ".env", backend_dir.parent / ".env"]


def _read_dotenv_value(path: Path, name: str) -> str:
    if not path.exists():
        return ""
    prefix = f"{name}="
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith(prefix):
            continue
        return stripped.split("=", 1)[1].strip().strip("\"'")
    return ""


def _aistudio_token_file() -> str:
    cache_home = Path(os.environ.get("AISTUDIO_CACHE_HOME") or Path.home())
    token_path = cache_home / ".cache" / "aistudio" / ".auth" / "token"
    if not token_path.exists():
        return ""
    return token_path.read_text(encoding="utf-8", errors="replace").strip()


def _metadata_page(metadata: dict[str, Any], fallback: int) -> int:
    for key in ("page", "page_number", "page_index"):
        value = metadata.get(key)
        try:
            page = int(value)
            return page + 1 if key == "page_index" and page == 0 else max(page, 1)
        except (TypeError, ValueError):
            continue
    return fallback


def _metadata_preview(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ("[raw response omitted]" if key == "paddleocr_vl_raw_response" else value)
        for key, value in metadata.items()
    }


def _raw_pages(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    pages = raw.get("pages")
    if isinstance(pages, list):
        return [page for page in pages if isinstance(page, dict)]
    results = raw.get("result", {}).get("layoutParsingResults") if isinstance(raw.get("result"), dict) else None
    if isinstance(results, list):
        return [page for page in results if isinstance(page, dict)]
    return []


def _image_refs(value: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = enumerate(value)
    else:
        return refs
    for key, item in items:
        refs.append(
            {
                "key": str(key),
                "kind": type(item).__name__,
                "preview": preview_text(str(item), 300) if isinstance(item, str) else "",
            }
        )
    return refs


def _blocks_from_raw_response(raw: Any, fallback_page: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return blocks
    pages = _raw_pages(raw) or [raw]
    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("pageNo") or page.get("page") or fallback_page or page_index)
        raw_blocks = _layout_blocks(page)
        for block_index, raw_block in enumerate(raw_blocks):
            if not isinstance(raw_block, dict):
                continue
            text = str(raw_block.get("text") or raw_block.get("content") or raw_block.get("markdown") or "").strip()
            block_type = str(raw_block.get("label") or raw_block.get("type") or "block").lower()
            block = make_block(
                LIBRARY_ID,
                page_number,
                "table" if "table" in block_type else "image" if "image" in block_type else "text",
                text or preview_text(str(raw_block), 500),
                provenance={
                    "source": "paddleocr_vl_raw_response.layoutBlocks",
                    "block_index": block_index,
                    "raw_type": block_type,
                },
            )
            if block:
                blocks.append(block)
    return blocks


def _layout_blocks(value: Any) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            direct = item.get("layoutBlocks") or item.get("layout_blocks") or item.get("blocks")
            if isinstance(direct, list):
                blocks.extend(block for block in direct if isinstance(block, dict))
            for child in item.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value.get("pruned_result") if isinstance(value, dict) else value)
    visit(value.get("raw") if isinstance(value, dict) else value)
    return blocks
