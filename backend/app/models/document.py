"""Pydantic models for document metadata."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentType(str, Enum):
    INVOICE = "invoice"
    RECEIPT = "receipt"
    CONTRACT = "contract"
    REPORT = "report"
    FORM = "form"
    ID = "id"
    OTHER = "other"


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    OCR_DONE = "ocr_done"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    FAILED = "failed"


class DocumentSource(str, Enum):
    UPLOAD = "upload"
    CORPORATE_DB = "corporate_db"


class DocumentMetadata(BaseModel):
    """Metadata describing an ingested document."""

    id: str
    name: str
    type: DocumentType = DocumentType.OTHER
    source: DocumentSource = DocumentSource.UPLOAD
    mime_type: str = "application/pdf"
    size_bytes: int = 0
    page_count: int = 0
    status: DocumentStatus = DocumentStatus.UPLOADED
    tags: list[str] = Field(default_factory=list)
    collection: Optional[str] = None  # corporate document collection/group
    uploaded_at: datetime = Field(default_factory=utcnow)
    processed_at: Optional[datetime] = None
    preview_url: Optional[str] = None
    confidence: Optional[float] = None  # overall OCR confidence 0..1
    notes: Optional[str] = None


class DocumentUploadAck(BaseModel):
    id: str
    name: str
    size_bytes: int
    status: DocumentStatus
    message: str = "Document uploaded successfully."
