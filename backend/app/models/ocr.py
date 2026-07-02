"""Pydantic models for OCR / extraction output."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel, Field

from app.models.document import utcnow


class BlockType(str, Enum):
    TEXT = "text"
    HEADING = "heading"
    TABLE = "table"
    KEY_VALUE = "key_value"
    IMAGE = "image"
    SIGNATURE = "signature"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class OcrBlock(BaseModel):
    """A single structural block extracted from a document page."""

    id: str
    page: int = 1
    type: BlockType = BlockType.TEXT
    bbox: Optional[list[float]] = None  # [x, y, w, h] in relative coords 0..1
    text: str = ""
    confidence: float = 0.95  # 0..1
    edited: bool = False
    # Optional structured payload for tables / key-value pairs
    data: Optional[dict[str, Any]] = None


class OcrResult(BaseModel):
    """Full OCR/extraction result for a document."""

    id: str
    document_id: str
    engine: str = "placeholder-ocr"  # e.g. "tesseract", "paddleocr", "cloud-vlm"
    language: str = "en"
    pages: int = 1
    blocks: list[OcrBlock] = Field(default_factory=list)
    overall_confidence: float = 0.0
    processed_at: datetime = Field(default_factory=utcnow)
    # Whether a human has edited the output
    edited: bool = False
    approved: bool = False


class OcrUpdate(BaseModel):
    """Partial update payload for editing OCR output."""

    blocks: Optional[list[OcrBlock]] = None
    approved: Optional[bool] = None
    engine: Optional[str] = None
