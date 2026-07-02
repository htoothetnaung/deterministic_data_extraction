"""Pydantic models for extraction templates."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from app.models.document import DocumentType, utcnow
from app.models.field import FieldType


class TemplateFieldDefinition(BaseModel):
    """Definition of a single field inside an extraction template."""

    id: str
    label: str
    key: str
    type: FieldType = FieldType.TEXT
    example_value: Optional[str] = None
    validation_rule: Optional[str] = None  # regex / rule expression
    required: bool = False
    notes: Optional[str] = None
    # extraction hint used by the (future) extraction engine
    extraction_hint: Optional[str] = None
    default_value: Optional[str] = None


class ExtractionTemplate(BaseModel):
    """A reusable extraction template."""

    id: str
    name: str
    description: Optional[str] = None
    document_type: DocumentType = DocumentType.OTHER
    fields: list[TemplateFieldDefinition] = Field(default_factory=list)
    # Advanced configuration mirroring the reference workflow
    ocr_method: str = "advanced-ocr-standard"
    chunking_strategy: str = "page-by-page"
    max_pages: int = 10
    loop_condition: Optional[str] = "EOF"
    version: str = "1.0.0"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    success_rate: Optional[float] = None  # benchmark success rate 0..1
    usage_count: int = 0
    source_document_id: Optional[str] = None  # doc used to create template


class TemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    document_type: DocumentType = DocumentType.OTHER
    fields: list[TemplateFieldDefinition] = Field(default_factory=list)
    ocr_method: str = "advanced-ocr-standard"
    chunking_strategy: str = "page-by-page"
    max_pages: int = 10
    loop_condition: Optional[str] = "EOF"
    source_document_id: Optional[str] = None
