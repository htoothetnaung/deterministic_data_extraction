"""API endpoints for lightweight benchmark history."""
from __future__ import annotations

import random

from fastapi import APIRouter, HTTPException

from app.data.mock import store
from app.models.benchmark import BenchmarkMetric, BenchmarkRun, BenchmarkRunCreate, BenchmarkStatus, FieldMetric, RunSummary
from app.models.document import utcnow

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


@router.get("", response_model=list[BenchmarkRun])
async def list_all():
    return list(store.benchmarks.values())


@router.get("/runs", response_model=list[RunSummary])
async def runs():
    return [_summary(run) for run in store.benchmarks.values()]


@router.post("", response_model=BenchmarkRun)
async def create(payload: BenchmarkRunCreate):
    tpl = store.templates.get(payload.template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    rng = random.Random(hash(payload.template_id) & 0xFFFF)
    field_metrics = [
        FieldMetric(
            field_key=field.key,
            label=field.label,
            accuracy=round(rng.uniform(0.8, 0.98), 3),
            exact_match=round(rng.uniform(0.75, 0.95), 3),
            missing_count=rng.randint(0, 1),
            correction_rate=round(rng.uniform(0.0, 0.2), 3),
            confidence=round(rng.uniform(0.7, 0.95), 3),
        )
        for field in tpl.fields
    ]
    accuracy = round(sum(item.accuracy for item in field_metrics) / max(len(field_metrics), 1), 3)
    latency = 120.0 + len(payload.document_ids) * 25.0
    run = BenchmarkRun(
        id=store.gen_id("bm"),
        run_id=store.gen_run_id(),
        template_id=tpl.id,
        template_name=tpl.name,
        document_ids=payload.document_ids,
        status=BenchmarkStatus.COMPLETED,
        finished_at=utcnow(),
        metrics=[
            BenchmarkMetric(name="field_level_accuracy", label="Field-level Accuracy", value=accuracy, target=0.95),
            BenchmarkMetric(name="processing_latency", label="Processing Latency", value=latency, unit="ms", target=1500.0),
        ],
        field_metrics=field_metrics,
        consistency_samples=[accuracy for _ in range(max(payload.repeat, 1))],
    )
    store.benchmarks[run.id] = run
    return run


@router.get("/{run_id}", response_model=BenchmarkRun)
async def get(run_id: str):
    for run in store.benchmarks.values():
        if run.id == run_id or run.run_id == run_id:
            return run
    raise HTTPException(status_code=404, detail="Benchmark run not found")


def _summary(run: BenchmarkRun) -> RunSummary:
    accuracy = next((metric.value for metric in run.metrics if metric.name == "field_level_accuracy"), None)
    latency = next((metric.value for metric in run.metrics if metric.name == "processing_latency"), None)
    return RunSummary(
        run_id=run.run_id,
        template_name=run.template_name,
        files_processed=len(run.document_ids),
        status=run.status,
        date=run.started_at,
        overall_accuracy=accuracy,
        latency_ms=latency,
    )
