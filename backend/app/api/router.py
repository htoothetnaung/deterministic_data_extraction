"""Aggregated API router mounting all endpoint modules."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.endpoints import batch, benchmarks, documents, ocr, templates

api_router = APIRouter()
api_router.include_router(documents.router)
api_router.include_router(ocr.router)
api_router.include_router(templates.router)
api_router.include_router(batch.router)
api_router.include_router(benchmarks.router)
