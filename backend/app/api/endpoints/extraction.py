"""Case-level extraction, search, and export APIs.

Coordinates query searching (RAG dense/sparse), triggering new case extractions, retrieving
job execution result statistics, re-evaluating fields (retries), and exporting results.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.db.engine import get_factory, is_db_configured
from app.models.extraction import ExportBundle, ExtractionRequest, ExtractionResult, SearchHit, SearchRequest
from app.services.extraction_platform import export_job, get_job, run_case_extraction, search_case, write_export_files
from app.services.production_extraction import (
    get_job_db,
    list_job_candidates_db,
    retry_field_db,
    run_case_extraction_db,
    search_case_db,
)

router = APIRouter(tags=["extraction"])


@router.post("/cases/{case_id}/search", response_model=list[SearchHit])
async def search(case_id: str, payload: SearchRequest):
    """Run a RAG hybrid search query across all document text chunks inside a Case.

    If database is configured, delegates to `search_case_db`, else falls back to in-memory store.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await search_case_db(session, case_id, payload)
    return search_case(case_id, payload)


@router.post("/cases/{case_id}/extract", response_model=ExtractionResult)
async def extract(case_id: str, payload: ExtractionRequest):
    """Trigger structured data extraction on a Case based on a target schema.

    If database is configured, delegates to `run_case_extraction_db` (RAG extraction loop),
    else falls back to synchronous keyword matching.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await run_case_extraction_db(session, case_id, payload)
    return run_case_extraction(case_id, payload)


@router.post("/cases/{case_id}/extract-baseline", response_model=ExtractionResult)
async def extract_baseline(case_id: str, payload: ExtractionRequest):
    """Run extraction baseline test comparisons (bypassing model retries/agent checks)."""
    return run_case_extraction(case_id, payload.model_copy(update={"baseline": True}))


@router.get("/extraction-jobs/{job_id}", response_model=ExtractionResult)
async def job(job_id: str):
    """Retrieve result statistics, values, and status for a specific ExtractionJob run.

    If database is configured, delegates to `get_job_db`, else falls back to in-memory store.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await get_job_db(session, job_id)
    return get_job(job_id)


@router.get("/extraction-jobs/{job_id}/candidates")
async def candidates(job_id: str):
    """List all raw value candidates generated for all fields during extraction attempts.

    If database is configured, delegates to `list_job_candidates_db`, else falls back to in-memory store.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await list_job_candidates_db(session, job_id)
    job_result = get_job(job_id)
    return [
        {**candidate.model_dump(mode="json"), "field_path": path}
        for path, field in job_result.fields.items()
        for candidate in field.candidates
    ]


@router.post("/extraction-jobs/{job_id}/fields/{field_path:path}/retry", response_model=ExtractionResult)
async def retry_field(job_id: str, field_path: str):
    """Manually re-trigger the extraction pipeline for a single target field.

    If database is configured, delegates to `retry_field_db`, else falls back to in-memory store.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await retry_field_db(session, job_id, field_path)
    return get_job(job_id)


@router.get("/extraction-jobs/{job_id}/export", response_model=ExportBundle)
async def export(job_id: str):
    """Export the completed extraction results as a structured text bundle."""
    return export_job(job_id)


@router.post("/extraction-jobs/{job_id}/export-files")
async def export_files(job_id: str):
    """Write the extraction results to physical files on disk and return paths."""
    return {"files": write_export_files(job_id)}
