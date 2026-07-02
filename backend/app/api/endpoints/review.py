"""Human review endpoints for extraction jobs."""
from __future__ import annotations

from fastapi import APIRouter

from app.models.extraction import ExtractionResult, FieldResult
from app.models.review import ApproveFieldRequest, CorrectFieldRequest, ReviewPayload
from app.services.extraction_platform import approve_field, correct_field, finalize_job, review_payload

router = APIRouter(prefix="/extraction-jobs/{job_id}", tags=["review"])


@router.get("/review", response_model=ReviewPayload)
async def review(job_id: str):
    return review_payload(job_id)


@router.post("/fields/{field_path}/approve", response_model=FieldResult)
async def approve(job_id: str, field_path: str, payload: ApproveFieldRequest):
    return approve_field(job_id, field_path, payload.reviewer_id, payload.reason)


@router.post("/fields/{field_path}/correct", response_model=FieldResult)
async def correct(job_id: str, field_path: str, payload: CorrectFieldRequest):
    return correct_field(job_id, field_path, payload.corrected_value, payload.reviewer_id, payload.reason)


@router.post("/finalize", response_model=ExtractionResult)
async def finalize(job_id: str):
    return finalize_job(job_id)
