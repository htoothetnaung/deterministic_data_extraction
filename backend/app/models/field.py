"""Pydantic models for editable extraction fields."""
from __future__ import annotations

from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel, Field


class FieldType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    CURRENCY = "currency"
    EMAIL = "email"
    PHONE = "phone"
    SELECT = "select"
    MULTISELECT = "multiselect"
    BOOLEAN = "boolean"
    TABLE = "table"
    REGEX = "regex"


class EditableExtractionField(BaseModel):
    """A single editable extraction field shown in the review UI."""

    id: str
    label: str
    key: str  # machine-readable key
    type: FieldType = FieldType.TEXT
    value: Optional[Any] = None
    raw_value: Optional[Any] = None  # original OCR value before editing
    confidence: float = 0.9
    confidence_level: str = "high"  # high | medium | low
    required: bool = False
    edited: bool = False
    valid: bool = True
    validation_message: Optional[str] = None
    options: Optional[list[str]] = None  # for select fields
    bbox: Optional[list[float]] = None  # link back to OCR block
    notes: Optional[str] = None


class FieldUpdate(BaseModel):
    """Payload for updating a single field's value during review."""

    value: Any = None
    notes: Optional[str] = None
