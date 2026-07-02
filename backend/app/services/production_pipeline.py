"""DB-backed document processing stages for production cases."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentModel, PageModel
from app.db.repositories.document_repo import DocumentRepository
from app.db.repositories.evidence_repo import EvidenceRepository
from app.models.parser_benchmark import ParserRunResult, ParserStatus
from app.services import document_parser
from app.services.artifact_store import ArtifactStore
from app.services.embedding import embed_texts
from app.services.evidence_cleaner import clean_parser_result
from app.services.parsers.orchestrator import PARSERS


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
    cleaned = clean_parser_result(result)
    ArtifactStore().store_parse_output(doc.case_id, doc.document_id, result_parser_id, result, cleaned)

    page_map = await _insert_pages(session, doc, result)
    evidence_repo = EvidenceRepository(session)
    for item in cleaned.get("items", []) if isinstance(cleaned, dict) else []:
        if isinstance(item, dict):
            page_id = page_map.get(int(item.get("page") or 1))
            await evidence_repo.create_from_clean_evidence(doc.case_id, doc.document_id, page_id, item)

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

