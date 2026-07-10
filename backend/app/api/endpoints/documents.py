"""API endpoints for managing documents.

Provides routes to upload files, retrieve file lists and details, delete documents,
trigger simulation processing, and query layout evidence chunks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile, HTTPException, Query

from app.core.config import settings
from app.db.engine import get_factory, is_db_configured
from app.data.mock import store
from app.models.document import (
    DocumentMetadata,
    DocumentSource,
    DocumentStatus,
    DocumentType,
    DocumentUploadAck,
)
from app.models.document import utcnow
from app.services.parsers import quick as document_parser
from app.services.production_extraction import list_document_evidence_db

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=list[DocumentMetadata])
async def list_documents(
    source: Optional[DocumentSource] = Query(None),
    type: Optional[DocumentType] = Query(None),
    collection: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search by name"),
):
    """Retrieve lists of uploaded documents, supporting filtering by type, source, or name queries."""
    docs = list(store.documents.values())
    if source:
        docs = [d for d in docs if d.source == source]
    if type:
        docs = [d for d in docs if d.type == type]
    if collection:
        docs = [d for d in docs if d.collection == collection]
    if q:
        ql = q.lower()
        docs = [d for d in docs if ql in d.name.lower()]
    # newest first
    docs.sort(key=lambda d: d.uploaded_at, reverse=True)
    return docs


@router.post("/upload", response_model=DocumentUploadAck)
async def upload_document(file: UploadFile = File(...)):
    """Upload a new document file. Stored on disk; metadata kept in-memory.

    Runs the quick parser to estimate page counts.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    # Save to disk
    save_path = Path(settings.upload_dir) / file.filename
    size = 0
    with open(save_path, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            size += len(chunk)

    # Run the placeholder parser to get page count
    parsed = document_parser.parse_document(save_path)

    did = store.gen_id("doc-upl")
    doc = DocumentMetadata(
        id=did,
        name=file.filename,
        type=DocumentType.OTHER,
        source=DocumentSource.UPLOAD,
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=size,
        page_count=int(parsed.get("page_count", 1)),
        status=DocumentStatus.UPLOADED,
        tags=["upload"],
        uploaded_at=utcnow(),
    )
    store.documents[did] = doc
    return DocumentUploadAck(
        id=did,
        name=doc.name,
        size_bytes=size,
        status=doc.status,
        message="Document uploaded successfully. Run processing to extract data.",
    )


@router.get("/{document_id}", response_model=DocumentMetadata)
async def get_document(document_id: str):
    """Retrieve details and processing state for a specific document by its ID."""
    doc = store.documents.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/{document_id}/evidence")
async def document_evidence(document_id: str):
    """Retrieve all layout and text chunks (evidence items) extracted from a document.

    Only available in database-backed modes.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await list_document_evidence_db(session, document_id)
    return []


@router.delete("/{document_id}")
async def delete_document(document_id: str):
    """Remove a document from in-memory persistence."""
    if store.documents.pop(document_id, None) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    store.ocr_results.pop(document_id, None)
    return {"ok": True, "id": document_id}


@router.post("/{document_id}/process", response_model=DocumentMetadata)
async def process_document(document_id: str):
    """Simulate parsing and OCR completion by marking document status to OCR_DONE."""
    doc = store.documents.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.status = DocumentStatus.PROCESSING
    # Simulate processing
    doc.status = DocumentStatus.OCR_DONE
    doc.processed_at = utcnow()
    doc.confidence = 0.87
    return doc
