"""Extraction schema CRUD APIs."""
from __future__ import annotations

from fastapi import APIRouter

from app.models.schema import ExtractionSchema, SchemaCreate, SchemaUpdate, SchemaValidationResult
from app.services.extraction_platform import (
    create_schema,
    get_schema,
    list_schemas,
    update_schema,
    validate_json_schema,
)

router = APIRouter(prefix="/schemas", tags=["schemas"])


@router.post("", response_model=ExtractionSchema)
async def create(payload: SchemaCreate):
    return create_schema(payload)


@router.get("", response_model=list[ExtractionSchema])
async def list_():
    return list_schemas()


@router.get("/{schema_id}", response_model=ExtractionSchema)
async def get(schema_id: str):
    return get_schema(schema_id)


@router.put("/{schema_id}", response_model=ExtractionSchema)
async def update(schema_id: str, payload: SchemaUpdate):
    return update_schema(schema_id, payload)


@router.post("/{schema_id}/validate", response_model=SchemaValidationResult)
async def validate_existing(schema_id: str):
    schema = get_schema(schema_id)
    return validate_json_schema(schema.json_schema)


@router.post("/validate", response_model=SchemaValidationResult)
async def validate(payload: dict):
    return validate_json_schema(payload)
