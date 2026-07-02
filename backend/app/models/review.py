"""Human review API models."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.document import utcnow
from app.models.extraction import ExtractionResult


ReviewActionType = Literal["approve", "correct", "mark_missing", "mark_not_applicable"]


class ReviewAction(BaseModel):
    review_id: str
    job_id: str
    field_path: str
    action: ReviewActionType
    old_value: Any = None
    corrected_value: Any = None
    reviewer_id: str = "local"
    reason: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ApproveFieldRequest(BaseModel):
    reviewer_id: str = "local"
    reason: str | None = None


class CorrectFieldRequest(BaseModel):
    corrected_value: Any
    reviewer_id: str = "local"
    reason: str | None = None


class ReviewPayload(BaseModel):
    job: ExtractionResult
    review_required_fields: list[str]
    actions: list[ReviewAction] = Field(default_factory=list)
