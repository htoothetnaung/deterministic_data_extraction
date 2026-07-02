"""API endpoints for batch template application."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.data.mock import store
from app.models.batch import BatchApplyRequest, BatchProcessingResult
from app.services.template_application import apply_template_batch

router = APIRouter(prefix="/batch", tags=["batch"])


@router.post("/apply", response_model=BatchProcessingResult)
async def apply(payload: BatchApplyRequest):
    if not store.templates.get(payload.template_id):
        raise HTTPException(status_code=404, detail="Template not found")
    docs = [d for d in payload.document_ids if d in store.documents]
    if not docs:
        raise HTTPException(status_code=400, detail="No valid documents provided")
    return apply_template_batch(payload.template_id, docs)


@router.get("", response_model=list[BatchProcessingResult])
async def list_batches():
    return list(store.batches.values())


@router.get("/{batch_id}", response_model=BatchProcessingResult)
async def get_batch(batch_id: str):
    b = store.batches.get(batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="Batch not found")
    return b
