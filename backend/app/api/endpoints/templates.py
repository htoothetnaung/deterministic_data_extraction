"""API endpoints for extraction templates."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.data.mock import store
from app.models.template import ExtractionTemplate, TemplateCreate
from app.services.template_service import (
    create_template,
    delete_template,
    get_template,
    list_templates,
    update_template,
)

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[ExtractionTemplate])
async def list_all():
    return list_templates()


@router.post("", response_model=ExtractionTemplate)
async def create(payload: TemplateCreate):
    return create_template(payload)


@router.get("/{template_id}", response_model=ExtractionTemplate)
async def get(template_id: str):
    tpl = get_template(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return tpl


@router.put("/{template_id}", response_model=ExtractionTemplate)
async def update(template_id: str, payload: dict):
    tpl = update_template(template_id, payload)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return tpl


@router.delete("/{template_id}")
async def remove(template_id: str):
    ok = delete_template(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"ok": True, "id": template_id}
