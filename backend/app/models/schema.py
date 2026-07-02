"""Schema models for production-style document extraction."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.document import utcnow


ConflictPolicy = Literal[
    "first_high_confidence",
    "highest_confidence",
    "human_review_on_disagreement",
    "allow_multiple_values",
]


class FieldExtractionHints(BaseModel):
    field_path: str
    description: str = ""
    expected_document_types: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    likely_regions: list[str] = Field(default_factory=list)
    value_type: str = "text"
    allow_multiple_sources: bool = True
    conflict_policy: ConflictPolicy = "human_review_on_disagreement"


class ExtractionSchema(BaseModel):
    schema_id: str
    user_id: str = "local"
    name: str
    json_schema: dict[str, Any]
    field_hints: dict[str, FieldExtractionHints] = Field(default_factory=dict)
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SchemaCreate(BaseModel):
    name: str
    json_schema: dict[str, Any]
    field_hints: dict[str, FieldExtractionHints] = Field(default_factory=dict)
    user_id: str = "local"


class SchemaUpdate(BaseModel):
    name: str | None = None
    json_schema: dict[str, Any] | None = None
    field_hints: dict[str, FieldExtractionHints] | None = None


class SchemaValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    field_paths: list[str] = Field(default_factory=list)
