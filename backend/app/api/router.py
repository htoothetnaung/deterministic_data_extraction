"""Aggregated API router mounting all endpoint modules."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.endpoints import (
    batch,
    benchmarks,
    cases,
    extraction,
    documents,
    extraction_lab,
    ocr,
    parser_benchmarks,
    review,
    schemas,
    templates,
)

api_router = APIRouter()
api_router.include_router(cases.router)
api_router.include_router(documents.router)
api_router.include_router(schemas.router)
api_router.include_router(extraction.router)
api_router.include_router(review.router)
api_router.include_router(ocr.router)
api_router.include_router(templates.router)
api_router.include_router(batch.router)
api_router.include_router(benchmarks.router)
api_router.include_router(parser_benchmarks.router)
api_router.include_router(extraction_lab.router)
