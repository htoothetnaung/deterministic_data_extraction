"""DB-backed document processing stages for production cases.

This module implements the document ingestion pipeline. It coordinates:
1. `quick_parse`: Cheap metadata extraction (file size, page counts, type detection) to immediately populate the UI.
2. `deep_parse`: Running heavyweight OCR/parsing engines (Docling, PaddleOCR, Mistral OCR), cleaning layouts, and storing raw page contents.
3. `index`: Computing OpenAI embeddings for text blocks and tables in batch and saving them to the pgvector retrieval index.
"""
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
from app.services.parsers import quick as document_parser
from app.services import evidence_cleaner
from app.services.chunk_indexer import index_chunks
from app.services.chunker import ChunkConfig, ChunkStrategy, chunk_parser_result
from app.services.embedding import embed_texts
from app.services.parsers.orchestrator import PARSERS

logger = logging.getLogger(__name__)

# Default strategy to chunk documents if the layout evidence cleaner is disabled.
DEFAULT_PRODUCTION_STRATEGY: ChunkStrategy = "page"

# Order of candidate parsers to attempt for deep parsing.
DEEP_PARSE_ORDER = ["mistral_ocr"]


async def parse_and_index_document(session: AsyncSession, document_id: str, parser_id: str | None = None) -> DocumentModel:
    """Execute the full document parsing and vector indexing pipeline.

    Workflow:
    1. Runs the Mistral OCR parser (via `_run_parser`) to extract layout blocks.
    2. Stores raw page transcripts into PageModel.
    3. Normalizes parser results using `evidence_cleaner.clean_parser_result`.
    4. Automatically generates OpenAI dense embeddings and persists chunks into the pgvector evidence index.
    5. Shifts document state directly to `"indexed"`.
    """
    repo = DocumentRepository(session)
    doc = await repo.get(document_id)
    if doc is None:
        raise ValueError(f"Document not found: {document_id}")
    if not doc.storage_path:
        raise ValueError(f"Document has no storage_path: {document_id}")

    result_parser_id, result = _run_parser(Path(doc.storage_path), parser_id)
    logger.info(
        "parse_and_index_document: parser=%s pages=%d chars=%d tables=%d images=%d",
        result_parser_id, result.pages or 0, result.chars or 0,
        result.tables or 0, result.images or 0,
    )

    # Store raw page transcripts for audit/reference in PageModel.
    page_map = await _insert_pages(session, doc, result)

    # Normalize layouts and clean duplicate content.
    cleaned = evidence_cleaner.clean_parser_result(result, max_pages=result.pages or 200)
    if cleaned.get("enabled") and cleaned.get("items"):
        logger.info(
            "parse_and_index_document: cleaner enabled, items=%d",
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
            "parse_and_index_document: indexed %d cleaned evidence items (cleaner=%s), "
            "openai_embeddings=%d, skipped_empty=%d",
            stats.chunks_indexed,
            cleaned.get("strategy", "unknown"),
            stats.openai_embeddings,
            stats.skipped_empty,
        )
    else:
        logger.info(
            "parse_and_index_document: cleaner disabled, reason=%s, falling back to raw chunker",
            cleaned.get("reason", "unknown"),
        )
        chunks = chunk_parser_result(
            result,
            strategy=DEFAULT_PRODUCTION_STRATEGY,
            config=ChunkConfig(max_pages=200),
        )
        logger.info(
            "parse_and_index_document: document_id=%s parser=%s pages=%d chunks=%d (cleaner disabled, fallback to raw chunker)",
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
            "parse_and_index_document: indexed %d chunks, openai_embeddings=%d, skipped_empty=%d",
            stats.chunks_indexed,
            stats.openai_embeddings,
            stats.skipped_empty,
        )

    parse_quality = _quality_from_result(result)
    await repo.update_parser_status(
        document_id,
        "indexed",
        parse_quality=parse_quality,
        confidence=_confidence_for_quality(parse_quality),
        page_count=max(doc.page_count, result.pages or 1),
        inferred_metadata={**(doc.inferred_metadata or {}), "parser_id": result_parser_id},
    )
    await session.commit()
    return doc


def _run_parser(path: Path, parser_id: str | None = None) -> tuple[str, ParserRunResult]:
    """Execute candidate parsers sequentially until one completes successfully.

    If a specific `parser_id` is supplied, only that parser is run.
    Otherwise, loops through `DEEP_PARSE_ORDER` as a fallback chain.
    """
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
    """Insert or update raw page transcripts into the PageModel database table.

    Splits the parsed text into page buckets and populates page quality metrics.
    """
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
    """Parse text structure to group segments into page numbers.

    Looks for standard page break markdown comments (`<!-- page: N -->`) inserted by parsers
    like Docling or Mistral OCR. Falls back to a single page if no markers are found.
    """
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
    """Assess parse text density quality based on average characters per page."""
    avg = (result.chars or len(result.raw_text or result.text_preview or "")) / max(result.pages or 1, 1)
    if avg < 80:
        return "poor"
    if avg < 300:
        return "medium"
    return "good"


def _quality_from_text(text: str) -> str:
    """Assess page text density quality based on length."""
    if len(text.strip()) < 80:
        return "poor"
    if len(text.strip()) < 300:
        return "medium"
    return "good"


def _confidence_for_quality(quality: str) -> float:
    """Assign a confidence score based on the predicted parse text quality."""
    return {"good": 0.92, "medium": 0.78, "poor": 0.45}.get(quality, 0.5)


def _priority_for_type(document_type: str) -> int:
    """Assign job queue priority numbers to documents depending on type.

    Prioritizes financial statements and annual reports over generic documents.
    """
    return {
        "financial_statement": 100,
        "annual_report": 90,
        "bank_statement": 80,
        "proxy_form": 50,
    }.get(document_type, 10)


def _clean_items_to_chunks(items: list[dict[str, Any]]) -> list[Chunk]:
    """Convert cleaned layout items from evidence_cleaner into standard Chunk objects.

    Reconstructs tables and maps coordinates/provenance before the chunks are indexed.
    """
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
