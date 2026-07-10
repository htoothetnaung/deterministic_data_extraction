"""Case-level extraction models with evidence and review state."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import utcnow


CaseStatus = Literal["open", "parsing", "indexed", "extracting", "needs_review", "completed", "failed"]
ExtractionJobStatus = Literal["queued", "running", "completed", "needs_review", "failed"]
FieldStatus = Literal["validated", "missing", "conflict", "low_confidence", "invalid", "human_corrected"]
ExtractionMethod = Literal["regex", "keyword_rule", "llm_text", "vlm_image", "table_parser", "human"]
SourceType = Literal["text_block", "table_cell", "table_row", "page", "image_region"]


class ExtractionCase(BaseModel):
    case_id: str
    user_id: str = "local"
    title: str
    status: CaseStatus = "open"
    document_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CaseCreate(BaseModel):
    title: str
    user_id: str = "local"
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class EvidenceSource(BaseModel):
    evidence_id: str
    document_id: str
    filename: str
    page_number: int
    source_type: SourceType = "text_block"
    text: str | None = None
    bbox: list[float] | dict[str, float] | None = None
    confidence: float | None = None


class TextBlock(BaseModel):
    block_id: str
    document_id: str
    page_number: int
    block_type: Literal["title", "paragraph", "table", "figure", "header", "footer", "list", "unknown"] = "paragraph"
    text: str
    bbox: list[float] | dict[str, float] | None = None
    reading_order: int | None = None
    section_title: str | None = None


class ParsedTable(BaseModel):
    table_id: str
    document_id: str
    page_number: int
    caption: str | None = None
    bbox: list[float] | dict[str, float] | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    markdown: str | None = None
    html: str | None = None


class ParsedPage(BaseModel):
    document_id: str
    page_number: int
    text: str
    markdown: str | None = None
    blocks: list[TextBlock] = Field(default_factory=list)
    width: float | None = None
    height: float | None = None
    image_path: str | None = None


class ParsedDocument(BaseModel):
    document_id: str
    case_id: str
    filename: str
    mime_type: str = "application/octet-stream"
    parser_name: str
    parser_version: str | None = None
    page_count: int
    document_type: str | None = None
    parse_quality: str | None = None
    pages: list[ParsedPage]
    tables: list[ParsedTable] = Field(default_factory=list)
    images: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FieldCandidate(BaseModel):
    candidate_id: str
    field_path: str
    value: Any
    normalized_value: Any | None = None
    confidence: float
    evidence: list[EvidenceSource]
    extraction_method: ExtractionMethod = "keyword_rule"
    validation_errors: list[str] = Field(default_factory=list)


class FieldResult(BaseModel):
    field_path: str
    value: Any | None = None
    status: FieldStatus
    confidence: float = 0.0
    selected_candidate_id: str | None = None
    candidates: list[FieldCandidate] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    job_id: str
    case_id: str
    schema_id: str
    status: ExtractionJobStatus
    fields: dict[str, FieldResult]
    final_json: dict[str, Any]
    validation_report: dict[str, Any]
    started_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_id: str
    output_schema: dict[str, Any] | None = Field(default=None, alias="schema_json")
    max_evidence_per_field: int = Field(default=8, ge=1, le=25)
    baseline: bool = False
    settings: dict[str, Any] | None = Field(default=None)


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=8, ge=1, le=50)


class SearchHit(BaseModel):
    score: float
    evidence: EvidenceSource


class ExportBundle(BaseModel):
    final_json: dict[str, Any]
    parsed_markdown: str
    evidence_report: dict[str, Any]
    validation_report: dict[str, Any]
    review_log: list[dict[str, Any]]
