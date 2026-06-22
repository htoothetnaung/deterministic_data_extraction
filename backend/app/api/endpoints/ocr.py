"""API endpoints for OCR / extraction results."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.data.mock import store
from app.models.field import EditableExtractionField, FieldType
from app.models.ocr import OcrResult, OcrUpdate
from app.models.document import utcnow

router = APIRouter(prefix="/ocr", tags=["ocr"])


@router.get("/{document_id}", response_model=OcrResult)
async def get_ocr(document_id: str):
    ocr = store.ocr_results.get(document_id)
    if not ocr:
        raise HTTPException(status_code=404, detail="OCR result not found. Process the document first.")
    return ocr


@router.put("/{document_id}", response_model=OcrResult)
async def update_ocr(document_id: str, payload: OcrUpdate):
    ocr = store.ocr_results.get(document_id)
    if not ocr:
        raise HTTPException(status_code=404, detail="OCR result not found")
    if payload.blocks is not None:
        ocr.blocks = payload.blocks
        ocr.edited = True
        ocr.overall_confidence = round(
            sum(b.confidence for b in ocr.blocks) / max(len(ocr.blocks), 1), 3
        )
    if payload.approved is not None:
        ocr.approved = payload.approved
        # also bump document status
        doc = store.documents.get(document_id)
        if doc:
            from app.models.document import DocumentStatus
            doc.status = DocumentStatus.APPROVED if payload.approved else DocumentStatus.REVIEWED
    ocr.processed_at = utcnow()
    return ocr


@router.post("/{document_id}/reset", response_model=OcrResult)
async def reset_ocr(document_id: str):
    """Reset edits back to the original OCR output (placeholder)."""
    ocr = store.ocr_results.get(document_id)
    if not ocr:
        raise HTTPException(status_code=404, detail="OCR result not found")
    for b in ocr.blocks:
        b.edited = False
    ocr.edited = False
    ocr.approved = False
    return ocr


@router.get("/{document_id}/fields", response_model=list[EditableExtractionField])
async def get_fields(document_id: str):
    """Return editable fields derived from the OCR result.

    PLACEHOLDER: derives fields from key-value blocks. Replace with a real
    field-mapping step driven by a template.
    """
    ocr = store.ocr_results.get(document_id)
    if not ocr:
        raise HTTPException(status_code=404, detail="OCR result not found")
    fields: list[EditableExtractionField] = []
    for b in ocr.blocks:
        if b.type.value == "key_value" and b.data:
            key = str(b.data.get("key", b.id)).lower().replace(" ", "_")
            value = b.data.get("value")
            conf = b.confidence
            level = "high" if conf >= 0.9 else "medium" if conf >= 0.7 else "low"
            fields.append(
                EditableExtractionField(
                    id=f"{document_id}:{key}",
                    label=str(b.data.get("key", b.id)),
                    key=key,
                    type=FieldType.TEXT,
                    value=value,
                    raw_value=value,
                    confidence=conf,
                    confidence_level=level,
                    required=False,
                    edited=False,
                    valid=True,
                    bbox=b.bbox,
                )
            )
    return fields
