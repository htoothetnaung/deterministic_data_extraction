"""Benchmark evaluation service.

PLACEHOLDER. Implement real benchmarking logic here.

Responsibilities:
  * Compare extracted values against ground-truth annotations.
  * Compute field-level accuracy, exact match, missing count, correction rate.
  * Measure processing latency.
  * Run repeated extractions to measure determinism / consistency.
  * Aggregate metrics into a ``BenchmarkRun``.

TODO: replace mock metric generation with a real evaluator.
"""
from __future__ import annotations

import random
from typing import Optional

from app.models.batch import BatchItemStatus
from app.models.benchmark import (
    BenchmarkMetric,
    BenchmarkRun,
    BenchmarkRunCreate,
    BenchmarkStatus,
    FieldMetric,
    RunSummary,
)
from app.services.template_application import apply_template_to_document


def _store():
    # Lazy import to avoid circular import with app.data.mock.
    from app.data.mock import store
    return store


# Stable metric labels used across the UI.
METRIC_DEFS = [
    ("field_level_accuracy", "Field-level Accuracy", "ratio", "Share of fields extracted correctly.", 0.95),
    ("exact_match_score", "Exact Match Score", "ratio", "Fraction of fields with an exact string match.", 0.92),
    ("ocr_correction_rate", "OCR Correction Rate", "ratio", "Fraction of fields corrected by a human.", 0.2),
    ("missing_field_count", "Missing Fields", "count", "Number of required fields not extracted.", 0.0),
    ("processing_latency", "Processing Latency", "ms", "Average end-to-end latency per document.", 1500.0),
    ("consistency_score", "Consistency (repeated runs)", "ratio", "Agreement across repeated runs of the same document.", 0.99),
    ("template_success_rate", "Template Success Rate", "ratio", "Share of documents processed without error.", 0.97),
]


def run_benchmark(payload: BenchmarkRunCreate) -> BenchmarkRun:
    """Run a benchmark for a template over a set of documents.

    TODO: replace mock scoring with a real comparison against ground truth.
    """
    tpl = _store().templates.get(payload.template_id)
    run = BenchmarkRun(
        id=_store().gen_id("bm"),
        run_id=_store().gen_run_id(),
        template_id=payload.template_id,
        template_name=tpl.name if tpl else "Unknown",
        document_ids=payload.document_ids,
        status=BenchmarkStatus.RUNNING,
    )

    # --- Placeholder: derive metrics from mock seed values + small noise ---
    rng = random.Random(hash(payload.template_id) & 0xFFFF)
    field_metrics: list[FieldMetric] = []
    for fdef in (tpl.fields if tpl else []):
        base = rng.uniform(0.8, 0.99)
        field_metrics.append(
            FieldMetric(
                field_key=fdef.key,
                label=fdef.label,
                accuracy=round(base, 3),
                exact_match=round(max(0.0, base - 0.05), 3),
                missing_count=rng.randint(0, 2),
                correction_rate=round(rng.uniform(0.0, 0.25), 3),
                confidence=round(rng.uniform(0.7, 0.98), 3),
            )
        )

    # Aggregate metrics
    overall_acc = round(sum(f.accuracy for f in field_metrics) / max(len(field_metrics), 1), 3)
    exact = round(sum(f.exact_match for f in field_metrics) / max(len(field_metrics), 1), 3)
    correction = round(sum(f.correction_rate for f in field_metrics) / max(len(field_metrics), 1), 3)
    missing = sum(f.missing_count for f in field_metrics)

    # Run template application to measure latency & success
    latencies = []
    success = 0
    for did in payload.document_ids:
        res = apply_template_to_document(payload.template_id, did)
        latencies.append(res.latency_ms)
        if res.status == BatchItemStatus.DONE:
            success += 1
    avg_latency = round(sum(latencies) / max(len(latencies), 1), 1) if latencies else 0.0
    success_rate = round(success / max(len(payload.document_ids), 1), 3)

    # Consistency across repeated runs
    consistency_samples = [round(overall_acc + rng.uniform(-0.01, 0.01), 3) for _ in range(max(payload.repeat, 1))]
    consistency = round(1.0 - (max(consistency_samples) - min(consistency_samples)), 3)

    run.metrics = [
        BenchmarkMetric(name="field_level_accuracy", label="Field-level Accuracy", value=overall_acc, unit="ratio", description="Share of fields extracted correctly.", target=0.95),
        BenchmarkMetric(name="exact_match_score", label="Exact Match Score", value=exact, unit="ratio", description="Fraction of fields with an exact string match.", target=0.92),
        BenchmarkMetric(name="ocr_correction_rate", label="OCR Correction Rate", value=correction, unit="ratio", description="Fraction of fields corrected by a human.", target=0.2),
        BenchmarkMetric(name="missing_field_count", label="Missing Fields", value=float(missing), unit="count", description="Number of required fields not extracted.", target=0.0),
        BenchmarkMetric(name="processing_latency", label="Processing Latency", value=avg_latency, unit="ms", description="Average end-to-end latency per document.", target=1500.0),
        BenchmarkMetric(name="consistency_score", label="Consistency (repeated runs)", value=consistency, unit="ratio", description="Agreement across repeated runs of the same document.", target=0.99),
        BenchmarkMetric(name="template_success_rate", label="Template Success Rate", value=success_rate, unit="ratio", description="Share of documents processed without error.", target=0.97),
    ]
    run.field_metrics = field_metrics
    run.consistency_samples = consistency_samples

    from app.models.document import utcnow

    run.finished_at = utcnow()
    run.status = BenchmarkStatus.COMPLETED
    _store().benchmarks[run.id] = run
    return run


def list_runs() -> list[RunSummary]:
    out: list[RunSummary] = []
    for run in _store().benchmarks.values():
        acc = next((m.value for m in run.metrics if m.name == "field_level_accuracy"), None)
        lat = next((m.value for m in run.metrics if m.name == "processing_latency"), None)
        out.append(
            RunSummary(
                run_id=run.run_id,
                template_name=run.template_name,
                files_processed=len(run.document_ids),
                status=run.status,
                date=run.started_at,
                overall_accuracy=acc,
                latency_ms=lat,
            )
        )
    return out


def get_run(run_id: str) -> Optional[BenchmarkRun]:
    # run_id may be the friendly RUN-... or the internal id
    for run in _store().benchmarks.values():
        if run.id == run_id or run.run_id == run_id:
            return run
    return None
