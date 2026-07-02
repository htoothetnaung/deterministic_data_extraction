"""Case-level extraction, search, and export APIs."""
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
    if is_db_configured():
        async with get_factory()() as session:
            return await search_case_db(session, case_id, payload)
    return search_case(case_id, payload)


@router.post("/cases/{case_id}/extract", response_model=ExtractionResult)
async def extract(case_id: str, payload: ExtractionRequest):
    if is_db_configured():
        async with get_factory()() as session:
            return await run_case_extraction_db(session, case_id, payload)
    return run_case_extraction(case_id, payload)


@router.post("/cases/{case_id}/extract-baseline", response_model=ExtractionResult)
async def extract_baseline(case_id: str, payload: ExtractionRequest):
    return run_case_extraction(case_id, payload.model_copy(update={"baseline": True}))


@router.get("/extraction-jobs/{job_id}", response_model=ExtractionResult)
async def job(job_id: str):
    if is_db_configured():
        async with get_factory()() as session:
            return await get_job_db(session, job_id)
    return get_job(job_id)


@router.get("/extraction-jobs/{job_id}/candidates")
async def candidates(job_id: str):
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
    if is_db_configured():
        async with get_factory()() as session:
            return await retry_field_db(session, job_id, field_path)
    return get_job(job_id)


@router.get("/extraction-jobs/{job_id}/export", response_model=ExportBundle)
async def export(job_id: str):
    return export_job(job_id)


@router.post("/extraction-jobs/{job_id}/export-files")
async def export_files(job_id: str):
    return {"files": write_export_files(job_id)}
