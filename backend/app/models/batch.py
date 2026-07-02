"""Pydantic models for batch template application."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel, Field

from app.models.document import utcnow
from app.models.field import EditableExtractionField


class BatchItemStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class BatchItemResult(BaseModel):
    """Result of applying a template to a single document."""

    document_id: str
    document_name: str
    status: BatchItemStatus = BatchItemStatus.DONE
    fields: list[EditableExtractionField] = Field(default_factory=list)
    overall_confidence: float = 0.0
    latency_ms: float = 0.0
    error: Optional[str] = None
    # diff vs. expected (for benchmarking)
    matched: int = 0
    mismatched: int = 0
    missing: int = 0


class BatchProcessingResult(BaseModel):
    """Aggregate result of applying a template to many documents."""

    id: str
    template_id: str
    template_name: str
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    total: int = 0
    done: int = 0
    failed: int = 0
    items: list[BatchItemResult] = Field(default_factory=list)
    average_confidence: float = 0.0
    average_latency_ms: float = 0.0


class BatchApplyRequest(BaseModel):
    template_id: str
    document_ids: list[str] = Field(default_factory=list)
