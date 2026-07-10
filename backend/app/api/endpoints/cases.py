"""Case-level document bundle APIs.

Handles creating Cases, listing recent Cases, uploading document files, querying Case progress,
and triggering parsing indexes. Supports database mode or local memory mock fallbacks.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.config import settings
from app.db.engine import get_factory, is_db_configured
from app.models.document import DocumentMetadata
from app.models.extraction import CaseCreate, ExtractionCase, ParsedDocument
from app.services.extraction_platform import attach_upload_to_case, create_case, get_case, list_cases, parse_case
from app.services.production_extraction import (
    attach_upload_to_case_db,
    create_case_db,
    get_case_db,
    get_case_progress_db,
    list_case_documents_db,
    list_cases_db,
)

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("", response_model=ExtractionCase)
async def create(payload: CaseCreate):
    """Create a new Case to group uploaded documents.

    If database is configured, delegates to `create_case_db`, else falls back to in-memory store.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await create_case_db(session, payload)
    return create_case(payload)


@router.get("", response_model=list[ExtractionCase])
async def list_():
    """List all recent Cases.

    If database is configured, delegates to `list_cases_db`, else falls back to in-memory store.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await list_cases_db(session)
    return list_cases()


@router.get("/{case_id}", response_model=ExtractionCase)
async def get(case_id: str):
    """Get metadata for a specific Case by its ID.

    If database is configured, delegates to `get_case_db`, else falls back to in-memory store.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await get_case_db(session, case_id)
    return get_case(case_id)


@router.get("/{case_id}/progress")
async def progress(case_id: str):
    """Query ingestion, parsing, and indexing progress statistics for all documents in a Case.

    If database is configured, delegates to `get_case_progress_db`, else falls back to in-memory store.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await get_case_progress_db(session, case_id)
    case = get_case(case_id)
    return {"case_id": case_id, "documents": {"total": len(case.document_ids)}, "status": case.status}


@router.get("/{case_id}/documents", response_model=list[DocumentMetadata])
async def case_documents(case_id: str):
    """List all documents attached to a specific Case.

    If database is configured, delegates to `list_case_documents_db`, else falls back to in-memory store.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await list_case_documents_db(session, case_id)
    case = get_case(case_id)
    from app.data.mock import store

    return [store.documents[doc_id] for doc_id in case.document_ids if doc_id in store.documents]


@router.post("/{case_id}/documents", response_model=DocumentMetadata)
async def upload_case_document(case_id: str, file: UploadFile = File(...), metadata_json: str = Form(default="{}")):
    """Upload a new document file and associate it with a specific Case.

    If database is configured:
    * Stores file on disk.
    * Computes SHA256 checksum hash.
    * Performs deduplication check.
    * Enqueues the 'quick_parse' task.
    """
    if is_db_configured():
        if not file.filename:
            raise HTTPException(status_code=400, detail="Missing filename")
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="metadata_json must be valid JSON") from exc
        file.file.seek(0)
        async with get_factory()() as session:
            return await attach_upload_to_case_db(
                session,
                case_id,
                file.file,
                file.filename,
                file.content_type or "application/octet-stream",
                metadata,
            )

    get_case(case_id)
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    original_name = Path(file.filename).name
    save_path = upload_dir / original_name
    if save_path.exists():
        save_path = upload_dir / f"{save_path.stem}-{uuid.uuid4().hex[:8]}{save_path.suffix}"

    size = 0
    with open(save_path, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            size += len(chunk)
    return attach_upload_to_case(case_id, save_path, file.content_type or "application/octet-stream", size)


@router.post("/{case_id}/index")
async def index_case(case_id: str):
    """Trigger or query document parsing and indexing status for the whole Case.

    If database is configured, returns status summary; in-memory runs synchronous parse adapter.
    """
    if is_db_configured():
        async with get_factory()() as session:
            docs = await list_case_documents_db(session, case_id)
            return [{"document_id": doc.id, "status": doc.status} for doc in docs]
    return parse_case(case_id)
