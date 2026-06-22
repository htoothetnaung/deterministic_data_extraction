"""API endpoints for benchmarking."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.data.mock import store
from app.models.benchmark import BenchmarkRun, BenchmarkRunCreate, RunSummary
from app.services.benchmark_service import get_run, list_runs, run_benchmark

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


@router.get("", response_model=list[BenchmarkRun])
async def list_all():
    return list(store.benchmarks.values())


@router.get("/runs", response_model=list[RunSummary])
async def runs():
    return list_runs()


@router.post("", response_model=BenchmarkRun)
async def create(payload: BenchmarkRunCreate):
    if not store.templates.get(payload.template_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return run_benchmark(payload)


@router.get("/{run_id}", response_model=BenchmarkRun)
async def get(run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    return run
