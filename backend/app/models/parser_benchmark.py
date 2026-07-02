"""Pydantic models for parser benchmark runs."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.document import utcnow


class ParserStatus(str, Enum):
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


class ParserInputInfo(BaseModel):
    id: str
    name: str
    input_type: str
    size_bytes: int
    path: str
    page_count: int = 1


class ParserInfo(BaseModel):
    id: str
    name: str
    supported_input_types: list[str]
    installed: bool
    notes: Optional[str] = None


class ParserRunRequest(BaseModel):
    input_id: str
    parsers: list[str] = Field(default_factory=list)
    preview_chars: int = Field(default=1500, ge=200, le=12000)


class ParserArtifactPaths(BaseModel):
    output_md: Optional[str] = None
    structured_json: Optional[str] = None
    corrections_json: Optional[str] = None


class ParserRunResult(BaseModel):
    result_id: str = ""
    run_id: str = ""
    library: str
    input_file: str
    input_type: str
    status: ParserStatus
    seconds: float
    pages: int
    chars: int
    tables: int
    images: int
    error: Optional[str] = None
    text_preview: str = ""
    structured_preview: dict[str, Any] = Field(default_factory=dict)
    artifact_paths: ParserArtifactPaths = Field(default_factory=ParserArtifactPaths)
    raw_text: str = Field(default="", exclude=True)


class ParserRunResponse(BaseModel):
    run_id: str = ""
    input: ParserInputInfo
    results: list[ParserRunResult]
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime = Field(default_factory=utcnow)


class ParserRunSummary(BaseModel):
    run_id: str
    input: ParserInputInfo
    parser_count: int
    ok: int
    skipped: int
    failed: int
    fastest_library: Optional[str] = None
    fastest_seconds: Optional[float] = None
    started_at: datetime
    finished_at: datetime


class ParserGroundTruthField(BaseModel):
    key: str
    label: str
    value: str


class ParserGroundTruth(BaseModel):
    input_id: str
    input_name: str = ""
    expected_terms: list[str] = Field(default_factory=list)
    expected_fields: list[ParserGroundTruthField] = Field(default_factory=list)
    notes: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class ParserCorrection(BaseModel):
    corrected_text: str = ""
    notes: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class ParserQualityCheck(BaseModel):
    key: str
    label: str
    expected: str
    found: bool
    confidence: float
    match_type: str


class ParserResultDetail(BaseModel):
    run: ParserRunResponse
    result: ParserRunResult
    full_text: str = ""
    ground_truth: ParserGroundTruth
    corrections: ParserCorrection
    quality_checks: list[ParserQualityCheck] = Field(default_factory=list)
