"""DB-backed document processing stages for production cases."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentModel, PageModel
from app.db.repositories.document_repo import DocumentRepository
from app.db.repositories.evidence_repo import EvidenceRepository
from app.models.parser_benchmark import ParserRunResult, ParserStatus
from app.services import document_parser
from app.services import evidence_cleaner
from app.services.chunk_indexer import index_chunks
from app.services.chunker import ChunkConfig, ChunkStrategy, chunk_parser_result
from app.services.embedding import embed_texts
from app.services.parsers.orchestrator import PARSERS

logger = logging.getLogger(__name__)

DEFAULT_PRODUCTION_STRATEGY: ChunkStrategy = "page"


DEEP_PARSE_ORDER = ["mistral_ocr", "paddleocr_vl_vllm", "layout_pdfplumber", "docling", "pdfplumber", "pymupdf", "pypdf"]


async def quick_parse_document(session: AsyncSession, document_id: str) -> DocumentModel:
    repo = DocumentRepository(session)
    doc = await repo.get(document_id)
    if doc is None:
        raise ValueError(f"Document not found: {document_id}")
    if not doc.storage_path:
        raise ValueError(f"Document has no storage_path: {document_id}")

    parsed = document_parser.parse_document(doc.storage_path)
    inferred = {
        **(doc.inferred_metadata or {}),
        "document_type": parsed.get("document_type") or "other",
        "first_page_text": str(parsed.get("first_page_text") or "")[:2000],
    }
    priority = _priority_for_type(inferred["document_type"])
    await repo.update_parser_status(
        document_id,
        "quick_parsed",
        page_count=int(parsed.get("page_count") or 1),
        inferred_metadata=inferred,
        priority=priority,
    )
    await repo.enqueue_job(document_id, "deep_parse", priority=priority)
    await session.commit()
    return doc


async def deep_parse_document(session: AsyncSession, document_id: str, parser_id: str | None = None) -> DocumentModel:
    repo = DocumentRepository(session)
    doc = await repo.get(document_id)
    if doc is None:
        raise ValueError(f"Document not found: {document_id}")
    if not doc.storage_path:
        raise ValueError(f"Document has no storage_path: {document_id}")

    result_parser_id, result = _run_parser(Path(doc.storage_path), parser_id)
    logger.info(
        "deep_parse_document: parser=%s pages=%d chars=%d tables=%d images=%d",
        result_parser_id, result.pages or 0, result.chars or 0,
        result.tables or 0, result.images or 0,
    )

    # Store raw parser output for audit (no cleaner pass — chunks go direct).
    page_map = await _insert_pages(session, doc, result)

    # Evidence cleaner: normalize parser output (tables, images, text) into
    # structured evidence items before indexing. Falls back to raw chunker
    # output when the parser is not supported by the cleaner.
    cleaned = evidence_cleaner.clean_parser_result(result, max_pages=result.pages or 200)
    if cleaned.get("enabled") and cleaned.get("items"):
        logger.info(
            "deep_parse_document: cleaner enabled, items=%d",
            len(cleaned["items"]),
        )
        chunk_dicts = cleaned["items"]
        stats = await index_chunks(
            session,
            doc.case_id,
            doc.document_id,
            _clean_items_to_chunks(chunk_dicts),
            embed_openai=True,
            embed_api=False,
            replace_existing=True,
        )
        logger.info(
            "deep_parse_document: indexed %d cleaned evidence items (cleaner=%s), "
            "openai_embeddings=%d, skipped_empty=%d",
            stats.chunks_indexed,
            cleaned.get("strategy", "unknown"),
            stats.openai_embeddings,
            stats.skipped_empty,
        )
    else:
        logger.info(
            "deep_parse_document: cleaner disabled, reason=%s, falling back to raw chunker",
            cleaned.get("reason", "unknown"),
        )
        chunks = chunk_parser_result(
            result,
            strategy=DEFAULT_PRODUCTION_STRATEGY,
            config=ChunkConfig(max_pages=200),
        )
        logger.info(
            "deep_parse_document: document_id=%s parser=%s pages=%d chunks=%d (cleaner disabled, fallback to raw chunker)",
            document_id,
            result_parser_id,
            result.pages or 1,
            len(chunks),
        )
        stats = await index_chunks(
            session,
            doc.case_id,
            doc.document_id,
            chunks,
            embed_openai=True,
            embed_api=False,
            replace_existing=True,
        )
        logger.info(
            "deep_parse_document: indexed %d chunks, openai_embeddings=%d, skipped_empty=%d",
            stats.chunks_indexed,
            stats.openai_embeddings,
            stats.skipped_empty,
        )

    parse_quality = _quality_from_result(result)
    await repo.update_parser_status(
        document_id,
        "parsed",
        parse_quality=parse_quality,
        confidence=_confidence_for_quality(parse_quality),
        page_count=max(doc.page_count, result.pages or 1),
        inferred_metadata={**(doc.inferred_metadata or {}), "parser_id": result_parser_id},
    )
    await repo.enqueue_job(document_id, "index", priority=doc.priority)
    await session.commit()
    return doc


async def index_document_evidence(session: AsyncSession, document_id: str) -> DocumentModel:
    repo = DocumentRepository(session)
    doc = await repo.get(document_id)
    if doc is None:
        raise ValueError(f"Document not found: {document_id}")
    evidence_repo = EvidenceRepository(session)
    items = await evidence_repo.list_by_document(document_id)
    text_items = [item for item in items if (item.text or item.markdown or "").strip()]
    texts = [(item.text or item.markdown or "")[:8000] for item in text_items]
    if texts:
        vectors = embed_texts(texts)
        for item, vector in zip(text_items, vectors):
            await evidence_repo.set_embedding(item.evidence_id, vector)
            await evidence_repo.refresh_search_vector(item.evidence_id)
    await repo.update_parser_status(document_id, "indexed")
    await session.commit()
    return doc


def _run_parser(path: Path, parser_id: str | None = None) -> tuple[str, ParserRunResult]:
    selected = [parser_id] if parser_id else DEEP_PARSE_ORDER
    skipped: list[str] = []
    for candidate in selected:
        if not candidate:
            continue
        module = PARSERS.get(candidate)
        if not module:
            skipped.append(f"{candidate}: unknown parser")
            continue
        suffix = path.suffix.lower().lstrip(".")
        input_type = "pdf" if suffix == "pdf" else "image" if suffix in {"png", "jpg", "jpeg", "webp", "tif", "tiff"} else "text"
        if input_type not in module.SUPPORTED_INPUT_TYPES or not module.is_available():
            skipped.append(f"{candidate}: unavailable")
            continue
        result = module.parse(path, preview_chars=20000)
        if result.status == ParserStatus.OK and (result.raw_text or result.text_preview or "").strip():
            return candidate, result
        skipped.append(f"{candidate}: {result.error or 'empty result'}")
    raise RuntimeError("; ".join(skipped) or "No parser could process document")


async def _insert_pages(session: AsyncSession, doc: DocumentModel, result: ParserRunResult) -> dict[int, str]:
    existing = await session.execute(select(PageModel).where(PageModel.document_id == doc.document_id))
    existing_pages = {page.page_number: page for page in existing.scalars().all()}
    page_texts = _split_pages(result.raw_text or result.text_preview or "", result.pages or doc.page_count or 1)
    page_map: dict[int, str] = {}
    for page_number, text in page_texts.items():
        page = existing_pages.get(page_number)
        if page is None:
            page = PageModel(
                document_id=doc.document_id,
                page_number=page_number,
                text=text,
                markdown=text,
                parse_quality=_quality_from_text(text),
            )
            session.add(page)
            await session.flush()
        else:
            page.text = text
            page.markdown = text
            page.parse_quality = _quality_from_text(text)
        page_map[page_number] = page.page_id
    return page_map


def _split_pages(text: str, page_count: int) -> dict[int, str]:
    import re

    markers = list(re.finditer(r"<!--\s*page:\s*(\d+)\s*-->", text, re.I))
    if not markers:
        return {1: text}
    pages: dict[int, str] = {}
    for index, marker in enumerate(markers):
        page = int(marker.group(1))
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        pages[page] = text[start:end].strip()
    for page in range(1, max(page_count, 1) + 1):
        pages.setdefault(page, "")
    return pages


def _quality_from_result(result: ParserRunResult) -> str:
    avg = (result.chars or len(result.raw_text or result.text_preview or "")) / max(result.pages or 1, 1)
    if avg < 80:
        return "poor"
    if avg < 300:
        return "medium"
    return "good"


def _quality_from_text(text: str) -> str:
    if len(text.strip()) < 80:
        return "poor"
    if len(text.strip()) < 300:
        return "medium"
    return "good"


def _confidence_for_quality(quality: str) -> float:
    return {"good": 0.92, "medium": 0.78, "poor": 0.45}.get(quality, 0.5)


def _priority_for_type(document_type: str) -> int:
    return {
        "financial_statement": 100,
        "annual_report": 90,
        "bank_statement": 80,
        "proxy_form": 50,
    }.get(document_type, 10)


def _clean_items_to_chunks(items: list[dict[str, Any]]) -> list[Chunk]:
    """Convert evidence_cleaner CleanEvidence dicts to Chunk objects for indexing."""
    from app.services.chunker import Chunk as _Chunk

    chunks: list[Chunk] = []
    for item in items:
        text = str(item.get("text") or "").strip()
        item_type = str(item.get("type") or "text").lower()
        rows = item.get("rows")
        if not text and item_type != "image" and not (isinstance(rows, list) and rows):
            continue
        if isinstance(rows, list) and rows and not text:
            text = "table evidence"
        chunks.append(
            _Chunk(
                chunk_id=str(item.get("id") or ""),
                page=int(item.get("page") or 1),
                chunk_type=item_type,
                text=text or "image evidence",
                bbox=item.get("bbox") if isinstance(item.get("bbox"), dict) else None,
                confidence=item.get("confidence") if isinstance(item.get("confidence"), (int, float)) else None,
                risk=str(item.get("risk") or "normal"),
                warnings=list(item.get("warnings") or []),
                source_url=(item.get("provenance") or {}).get("url") if isinstance(item.get("provenance"), dict) else None,
                columns=list(item.get("columns")) if isinstance(item.get("columns"), list) else None,
                rows=list(rows) if isinstance(rows, list) else None,
                table_index=item.get("table_index"),
                row_index=item.get("row_index"),
                header=list(item.get("header")) if isinstance(item.get("header"), list) else None,
                token_count=item.get("token_count"),
                strategy="block",
                metadata=item.get("provenance") if isinstance(item.get("provenance"), dict) else {},
            )
        )
    return chunks
