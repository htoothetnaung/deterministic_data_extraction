"""Pydantic models for benchmarking runs & metrics."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models.document import utcnow


class BenchmarkStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BenchmarkMetric(BaseModel):
    """A single benchmark metric."""

    name: str  # e.g. "field_level_accuracy"
    label: str  # e.g. "Field-level Accuracy"
    value: float  # 0..1 (or latency in ms when unit == "ms")
    unit: str = "ratio"  # ratio | ms | count | percent
    description: Optional[str] = None
    target: Optional[float] = None  # target/threshold value


class FieldMetric(BaseModel):
    """Per-field benchmark breakdown."""

    field_key: str
    label: str
    accuracy: float  # 0..1
    exact_match: float  # 0..1
    missing_count: int = 0
    correction_rate: float = 0.0
    confidence: float = 0.0


class RunSummary(BaseModel):
    """One row in the extraction history table."""

    run_id: str
    template_name: str
    files_processed: int
    status: BenchmarkStatus
    date: datetime
    overall_accuracy: Optional[float] = None
    latency_ms: Optional[float] = None


class BenchmarkRun(BaseModel):
    """A full benchmark run."""

    id: str
    run_id: str  # human-friendly run id, e.g. RUN-2024-0001
    template_id: str
    template_name: str
    document_ids: list[str] = Field(default_factory=list)
    status: BenchmarkStatus = BenchmarkStatus.COMPLETED
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    metrics: list[BenchmarkMetric] = Field(default_factory=list)
    field_metrics: list[FieldMetric] = Field(default_factory=list)
    # Determinism: accuracy across N repeated runs of the same document
    consistency_samples: list[float] = Field(default_factory=list)
    notes: Optional[str] = None


class BenchmarkRunCreate(BaseModel):
    template_id: str
    document_ids: list[str] = Field(default_factory=list)
    repeat: int = 1  # number of repeated runs for consistency measurement
