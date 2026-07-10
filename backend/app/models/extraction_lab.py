"""Pydantic models for schema-driven extraction lab runs."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.document import utcnow
from app.models.parser_benchmark import ParserInputInfo


class ExtractionFieldType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    CURRENCY = "currency"
    EMAIL = "email"
    PHONE = "phone"
    BOOLEAN = "boolean"
    LIST = "list"
    TABLE = "table"
    OBJECT = "object"


class ExtractionTier(str, Enum):
    COST_EFFECTIVE = "cost_effective"
    AGENTIC = "agentic"
    AGENTIC_PLUS = "agentic_plus"


class ExtractionSchemaField(BaseModel):
    """User-defined output field for a lab extraction schema."""

    id: str = ""
    key: str
    label: str = ""
    type: ExtractionFieldType = ExtractionFieldType.TEXT
    description: Optional[str] = None
    required: bool = False
    children: list["ExtractionSchemaField"] = Field(default_factory=list)


class ExtractionLabSchema(BaseModel):
    """Top-level schema definition used to build a dynamic Pydantic model."""

    name: str = "ExtractionResult"
    description: Optional[str] = None
    fields: list[ExtractionSchemaField] = Field(default_factory=list)


class ExtractionLabSchemaTemplate(BaseModel):
    """Schema template loaded from data/extraction_schemas."""

    id: str
    label: str
    filename: str
    schema_definition: ExtractionLabSchema = Field(serialization_alias="schema")


class ExtractionRunRequest(BaseModel):
    input_id: str
    output_schema: ExtractionLabSchema
    natural_language_query: Optional[str] = None
    parser_id: str = "auto"
    chunking_strategy: str = "page"
    chunk_size: int = Field(default=500, ge=64, le=8000)
    chunk_overlap: int = Field(default=80, ge=0, le=2048)
    max_pages: int = Field(default=50, ge=1, le=500)
    max_candidates_per_field: int = Field(default=8, ge=1, le=25)
    preview_chars: int = Field(default=6000, ge=1000, le=20000)
    extraction_tier: ExtractionTier = ExtractionTier.COST_EFFECTIVE
    settings: Optional[dict[str, Any]] = None


class MultiDocumentMode(str, Enum):
    PER_DOCUMENT = "per_document"
    CROSS_DOCUMENT = "cross_document"


class MultiDocumentExtractionRunRequest(ExtractionRunRequest):
    input_ids: list[str] = Field(default_factory=list, min_length=1, max_length=100)
    multi_document_mode: MultiDocumentMode = MultiDocumentMode.PER_DOCUMENT


class MultiDocumentExtractionRunResponse(BaseModel):
    mode: MultiDocumentMode
    results: list["ExtractionRunResponse"]


class SchemaGenerationRequest(BaseModel):
    input_ids: list[str] = Field(default_factory=list, max_length=20)
    natural_language_query: Optional[str] = None
    parser_id: str = "auto"
    multi_document_mode: MultiDocumentMode = MultiDocumentMode.PER_DOCUMENT
    chunking_strategy: str = "page"
    chunk_size: int = Field(default=500, ge=64, le=8000)
    chunk_overlap: int = Field(default=80, ge=0, le=2048)
    max_pages: int = Field(default=20, ge=1, le=500)
    preview_chars: int = Field(default=8000, ge=1000, le=20000)


class SchemaGenerationResponse(BaseModel):
    schema_definition: ExtractionLabSchema
    warnings: list[str] = Field(default_factory=list)


class ExtractionEvidence(BaseModel):
    chunk_id: str
    page: int
    type: str
    text_preview: str
    bbox: Optional[dict[str, float]] = None
    source_url: Optional[str] = None


class ExtractionChunk(BaseModel):
    id: str
    page: int
    type: str
    char_count: int
    text_preview: str
    bbox: Optional[dict[str, float]] = None
    confidence: Optional[float] = None
    risk: str = "normal"
    warnings: list[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, str]] = Field(default_factory=list)
    strategy: str = "page"
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    header: Optional[list[str]] = None
    token_count: Optional[int] = None


class ExtractionFieldResult(BaseModel):
    key: str
    label: str
    type: ExtractionFieldType
    required: bool
    value: Any = None
    raw_value: Any = None
    confidence: float = 0.0
    valid: bool = True
    validation_message: Optional[str] = None
    evidence: list[ExtractionEvidence] = Field(default_factory=list)


class ExtractionValidationError(BaseModel):
    loc: str
    msg: str
    type: str


class ExtractionRunStats(BaseModel):
    parser_seconds: float
    total_seconds: float
    pages: int
    chunks: int
    fields: int
    candidates_scanned: int
    chunking_strategy: str = "page"
    chunk_tokens: int = 0
    # Retrieval mode: "full_pipeline" (dense + BM25), "bm25_only", "dense_only", or "fts_fallback".
    retrieval_mode: str = "unknown"
    dense_hits: int = 0
    sparse_hits: int = 0
    # Deprecated — kept for backwards compatibility with older cached results.
    cleaned_evidence_used: bool = False
    cleaned_evidence_items: int = 0
    llm_reconstruction_used: bool = False
    llm_reconstruction_items: int = 0
    null_fields_detected: int = 0
    null_retries: int = 0
    recovered_nulls: int = 0
    candidate_conflicts: int = 0
    critic_issues: int = 0
    consistency_score: float = 1.0
    agentic_used: bool = False
    adk_available: bool = False
    model_used: Optional[str] = None


class ExtractionRunResponse(BaseModel):
    run_id: str
    input: ParserInputInfo
    parser_id: str
    parser_name: str
    parser_run_id: Optional[str] = None
    parser_run_started_at: Optional[datetime] = None
    extraction_tier: ExtractionTier = ExtractionTier.COST_EFFECTIVE
    schema_model_name: str
    schema_definition: dict[str, Any]
    natural_language_query: Optional[str] = None
    data: dict[str, Any]
    fields: list[ExtractionFieldResult]
    chunks: list[ExtractionChunk]
    validation_errors: list[ExtractionValidationError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_code: str
    stats: ExtractionRunStats
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime = Field(default_factory=utcnow)


class ExtractionReportRequest(BaseModel):
    result: ExtractionRunResponse


class ExtractionReportResponse(BaseModel):
    report_markdown: str


class JobHistoryItem(BaseModel):
    job_id: str
    filename: str
    status: str  # PENDING | RUNNING | SUCCESS | FAILED
    tier: str    # Cost Effective | Agentic
    queue_time: str
    processing_time: str
    total_time: str
    estimated_cost_usd: float = 0.0
    created_at: str
    result_run_id: Optional[str] = None
