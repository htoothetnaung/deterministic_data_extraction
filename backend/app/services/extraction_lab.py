"""Schema-driven extraction service for the Extraction Lab.

The pipeline is intentionally chunk-first:
  1. Parse the document with an existing parser adapter (Mistral OCR first).
  2. Build page/block chunks from parser structure.
  3. Retrieve candidate chunks per schema field.
  4. Extract values with the OpenAI LLM (LLM-first), grounding it on the
     candidate chunks; fall back to deterministic regex/label extraction only
     when the LLM is unavailable.
  5. Coerce values into Python data structures.
  6. Validate the final output with a dynamic Pydantic model.

Candidate-chunk retrieval keeps the LLM prompt small and grounded in evidence,
while the LLM does the actual value interpretation the regexes could not.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Union, get_args, get_origin

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ConfigDict, Field as PydanticField, ValidationError, create_model

from app.db.models import EvidenceItemModel
from app.db.repositories.case_repo import CaseRepository
from app.db.repositories.document_repo import DocumentRepository
from app.core.config import runtime_env_value
from app.models.document import utcnow
from app.models.extraction import ExtractionRequest, ExtractionResult
from app.extraction.prompts import SINGLE_FIELD_LLM_PROMPT, SCHEMA_GENERATION_SYSTEM_PROMPT
from app.models.extraction_lab import (
    ExtractionChunk,
    ExtractionEvidence,
    ExtractionFieldResult,
    ExtractionFieldType,
    ExtractionLabSchema,
    ExtractionLabSchemaTemplate,
    MultiDocumentExtractionRunRequest,
    MultiDocumentExtractionRunResponse,
    MultiDocumentMode,
    ExtractionRunRequest,
    ExtractionRunResponse,
    ExtractionRunStats,
    ExtractionSchemaField,
    ExtractionTier,
    ExtractionValidationError,
    SchemaGenerationRequest,
    SchemaGenerationResponse,
)
from app.models.parser_benchmark import ParserInputInfo, ParserRunResult, ParserStatus
from app.services.parsers.base import ok_result, preview_text, resolve_input
from app.services.parsers.orchestrator import PARSERS
from app.services.parsers.persistence import get_latest_ok_result_for_input
from app.services.chunk_indexer import index_chunks
from app.services import evidence_cleaner
from app.services.chunker import (
    DEFAULT_STRATEGY as DEFAULT_CHUNK_STRATEGY,
    Chunk,
    ChunkConfig,
    ChunkStrategy,
    chunk_parser_result,
)
from app.services.production_extraction import run_case_extraction_db

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

SUPPORTED_EXTRACTION_PARSERS = {"mistral_ocr"}
# Mistral OCR is the primary parser: it produces the cleanest markdown for the
# LLM extractor.
AUTO_PARSER_ORDER = ["mistral_ocr"]
MAIN_PARSER_ID = "mistral_ocr"
OPENAI_RECONSTRUCTION_MODEL = "gpt-5-mini"
OPENAI_SCHEMA_MODEL = "gpt-5-mini"
ALLOWED_GENERATED_FIELD_TYPES = {
    ExtractionFieldType.TEXT,
    ExtractionFieldType.NUMBER,
    ExtractionFieldType.BOOLEAN,
    ExtractionFieldType.OBJECT,
    ExtractionFieldType.LIST,
}

TYPE_PATTERNS: dict[ExtractionFieldType, re.Pattern[str]] = {
    ExtractionFieldType.EMAIL: re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    ExtractionFieldType.PHONE: re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)"),
    ExtractionFieldType.DATE: re.compile(
        r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?\d{4})\b",
        re.I,
    ),
    ExtractionFieldType.CURRENCY: re.compile(r"(?<!\w)(?:[$€£]\s*)?-?\(?\d[\d,]*(?:\.\d{2})?\)?(?:\s?(?:USD|MMK|EUR|GBP))?(?!\w)", re.I),
    ExtractionFieldType.NUMBER: re.compile(r"(?<![\w.-])-?\d+(?:,\d{3})*(?:\.\d+)?(?![\w.-])"),
    ExtractionFieldType.BOOLEAN: re.compile(r"\b(?:true|false|yes|no|pass|fail|approved|rejected|compliant|non-compliant)\b", re.I),
}
    

def list_schema_templates() -> list[ExtractionLabSchemaTemplate]:
    """Retrieve and list all predefined JSON schema templates stored in the data directory.

    Reads property definitions, fields types, and description guides from files located
    at `data/extraction_schemas/*.json` to populate the frontend schema drop-down.
    """
    schema_dir = Path(__file__).resolve().parents[3] / "data" / "extraction_schemas"
    if not schema_dir.exists():
        return []

    templates: list[ExtractionLabSchemaTemplate] = []
    for path in sorted(schema_dir.glob("*.json"), key=lambda item: item.name.lower()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            schema = ExtractionLabSchema.model_validate(payload)
        except Exception:
            continue
        templates.append(
            ExtractionLabSchemaTemplate(
                id=path.stem,
                label=_template_label(path.stem, schema.name),
                filename=path.name,
                schema_definition=schema,
            )
        )
    return templates


def generate_schema_definition(payload: SchemaGenerationRequest) -> SchemaGenerationResponse:
    """Analyze a document and a natural language query to automatically build a target JSON extraction schema.

    Uses an LLM (GPT) to read a representative sample of page text and layout structures from
    the selected document, generating a structured set of fields, types, and descriptions.
    Falls back to a deterministic regex-based context schema draft if the OpenAI API is unconfigured.
    """
    query = (payload.natural_language_query or "").strip()
    input_ids = list(dict.fromkeys(payload.input_ids))[:1]
    if not input_ids:
        raise HTTPException(status_code=400, detail="Select at least one parsed document before generating a schema")

    warnings: list[str] = []
    evidence: list[dict[str, Any]] = []
    for input_id in input_ids:
        input_info = resolve_input(input_id)
        if not input_info:
            raise HTTPException(status_code=404, detail=f"Extraction input not found: {input_id}")
        request = ExtractionRunRequest(
            input_id=input_id,
            output_schema=ExtractionLabSchema(),
            natural_language_query=query or None,
            parser_id=payload.parser_id,
            chunking_strategy=payload.chunking_strategy,
            chunk_size=payload.chunk_size,
            chunk_overlap=payload.chunk_overlap,
            max_pages=payload.max_pages,
            preview_chars=payload.preview_chars,
        )
        try:
            parser_id, parser_name, parser_result, _started_at = _load_parser_result(request)
        except HTTPException as exc:
            warnings.append(f"{input_info.name}: {exc.detail}")
            continue
        chunks = _build_chunks(parser_result, payload.max_pages, request)
        if not chunks:
            chunks = [SourceChunk(id="document-1", page=1, type="text", text=parser_result.raw_text or parser_result.text_preview or "")]
        for chunk in _schema_context_chunks(chunks):
            evidence.append(
                {
                    "document": input_info.name,
                    "parser": parser_name,
                    "parser_id": parser_id,
                    "page": chunk.page,
                    "type": chunk.type,
                    "text": preview_text(chunk.text, 2200),
                    "columns": chunk.columns or [],
                    "rows": (chunk.rows or [])[:5],
                }
            )
    if not evidence:
        detail = "; ".join(warnings) if warnings else "No parser evidence was available"
        raise HTTPException(
            status_code=422,
            detail=f"Schema generation requires parser output from the selected document(s). {detail}",
        )

    api_key = _openai_api_key()
    if api_key:
        schema = _call_openai_schema_generator(api_key, query, evidence, payload.multi_document_mode)
    else:
        warnings.append("OPENAI_API_KEY is not set; generated a deterministic draft from parser evidence only.")
        schema = _fallback_schema_from_context(query, evidence)
    return SchemaGenerationResponse(schema_definition=schema, warnings=warnings)


def _schema_context_chunks(chunks: list[SourceChunk]) -> list[SourceChunk]:
    selected: list[SourceChunk] = []
    selected.extend([chunk for chunk in chunks if chunk.type == "table"][:8])
    selected.extend([chunk for chunk in chunks if chunk.type != "table"][:18])
    deduped: dict[str, SourceChunk] = {}
    for chunk in selected:
        if chunk.text.strip():
            deduped.setdefault(chunk.id, chunk)
    return list(deduped.values())[:24]


@dataclass
class SourceChunk:
    id: str
    page: int
    type: str
    text: str
    bbox: Optional[dict[str, float]] = None
    confidence: Optional[float] = None
    risk: str = "normal"
    warnings: list[str] | None = None
    source_url: Optional[str] = None
    columns: list[str] | None = None
    rows: list[dict[str, str]] | None = None
    strategy: str = "block"
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    header: list[str] | None = None
    token_count: Optional[int] = None


@dataclass
class Candidate:
    chunk: SourceChunk
    score: float


def run_extraction(payload: ExtractionRunRequest) -> ExtractionRunResponse:
    input_info = resolve_input(payload.input_id)
    if not input_info:
        raise HTTPException(status_code=404, detail="Extraction input not found")
    if not payload.output_schema.fields:
        raise HTTPException(status_code=400, detail="Schema must include at least one field")

    start = time.perf_counter()
    started_at = utcnow()
    parser_id, parser_name, parser_result, parser_run_started_at = _load_parser_result(payload)
    chunks = _build_chunks(parser_result, payload.max_pages, payload)
    chunking_strategy_used = chunks[0].strategy if chunks else payload.chunking_strategy
    if not chunks:
        chunks = [
            SourceChunk(
                id="document-1",
                page=1,
                type="text",
                text=parser_result.raw_text or parser_result.text_preview or "",
            )
        ]

    search_schema = _schema_with_query(payload.output_schema, payload.natural_language_query)

    data: dict[str, Any] = {}
    field_results: list[ExtractionFieldResult] = []
    candidates_scanned = 0
    search_fields = {field.key: field for field in search_schema.fields}
    for field in payload.output_schema.fields:
        search_field = search_fields.get(field.key, field)
        result, candidate_count = _extract_field(search_field, chunks, payload.max_candidates_per_field)
        if search_field is not field:
            result = result.model_copy(
                update={
                    "key": field.key,
                    "label": field.label,
                    "type": field.type,
                    "required": field.required,
                }
            )
        field_results.append(result)
        data[field.key] = result.value
        candidates_scanned += candidate_count

    model_name, dynamic_model, generated_code = _build_pydantic_model(payload.output_schema)
    validation_errors = _validate_required(payload.output_schema, data)
    try:
        model_instance = dynamic_model.model_validate(data)
        data = model_instance.model_dump(mode="json", by_alias=True)
    except ValidationError as exc:
        validation_errors.extend(_format_validation_errors(exc))
        data = _json_safe(data)

    error_by_key = _errors_by_key(validation_errors)
    normalized_field_results: list[ExtractionFieldResult] = []
    for result in field_results:
        message = error_by_key.get(result.key)
        normalized_field_results.append(
            result.model_copy(
                update={
                    "valid": message is None,
                    "validation_message": message,
                    "value": data.get(result.key),
                }
            )
        )

    finished_at = utcnow()
    total_seconds = time.perf_counter() - start
    warnings = _run_warnings(parser_result, chunks, validation_errors)
    res = ExtractionRunResponse(
        run_id=f"ext-{uuid.uuid4().hex[:10]}",
        input=input_info,
        parser_id=parser_id,
        parser_name=parser_name,
        parser_run_id=parser_result.run_id or None,
        parser_run_started_at=parser_run_started_at,
        extraction_tier=payload.extraction_tier,
        schema_model_name=model_name,
        schema_definition=payload.output_schema.model_dump(mode="json"),
        natural_language_query=payload.natural_language_query,
        data=data,
        fields=normalized_field_results,
        chunks=[_chunk_to_extraction_chunk(chunk) for chunk in chunks[:300]],
        validation_errors=validation_errors,
        warnings=warnings,
        generated_code=generated_code,
        stats=ExtractionRunStats(
            parser_seconds=parser_result.seconds,
            total_seconds=round(total_seconds, 4),
            pages=parser_result.pages,
            chunks=len(chunks),
            fields=len(payload.output_schema.fields),
            candidates_scanned=candidates_scanned,
            chunking_strategy=chunking_strategy_used,
            chunk_tokens=sum(chunk.token_count or 0 for chunk in chunks),
            retrieval_mode="in_memory",
            dense_hits=0,
            sparse_hits=0,
        ),
        started_at=started_at,
        finished_at=finished_at,
    )
    return enrich_response_bboxes(res)


def _replace_filename_in_description(description: Optional[str], target_filename: str) -> Optional[str]:
    if not description:
        return description
    pattern = r'[^\"\'\n;,\.\:]+\.pdf'
    return re.sub(pattern, target_filename, description, flags=re.IGNORECASE)


def _replace_filename_in_field(field: ExtractionSchemaField, target_filename: str) -> None:
    if field.description:
        field.description = _replace_filename_in_description(field.description, target_filename)
    for child in field.children:
        _replace_filename_in_field(child, target_filename)


def _replace_filename_in_schema(schema: ExtractionLabSchema, target_filename: str) -> ExtractionLabSchema:
    for field in schema.fields:
        _replace_filename_in_field(field, target_filename)
    return schema


async def run_extraction_db(session: AsyncSession, payload: ExtractionRunRequest) -> ExtractionRunResponse:
    """Run sandboxed extraction by mapping requests into the database-backed case engine.

    To guarantee exact parity between production runs and sandbox benches, this method:
    1. Creates a temporary synthetic case and document.
    2. Runs evidence cleaning and indexes layout chunks into pgvector and FTS.
    3. Invokes `run_case_extraction_db` to run the production RAG/LLM pipelines.
    4. Caches successful runs (free of validation errors) using a SHA256 payload hash
       to accelerate future runs.
    """
    input_info = resolve_input(payload.input_id)
    if not input_info:
        raise HTTPException(status_code=404, detail="Extraction input not found")
    
    # Update the schema descriptions dynamically with the actual current input document name!
    payload.output_schema = _replace_filename_in_schema(payload.output_schema.model_copy(deep=True), input_info.name)

    if not payload.output_schema.fields:
        raise HTTPException(status_code=400, detail="Schema must include at least one field")

    start = time.perf_counter()
    started_at = utcnow()
    logger.info("extraction_lab: run_extraction_db document=%s fields=%d", payload.input_id, len(payload.output_schema.fields))
    parser_id, parser_name, parser_result, parser_run_started_at = _load_parser_result(payload)
    logger.info("extraction_lab: parsed pages=%d parser=%s", parser_result.pages, parser_result.library)
    chunks = _build_chunks(parser_result, payload.max_pages, payload)
    chunking_strategy_used = chunks[0].strategy if chunks else payload.chunking_strategy
    if not chunks:
        chunks = [
            SourceChunk(
                id="document-1",
                page=1,
                type="text",
                text=parser_result.raw_text or parser_result.text_preview or "",
            )
        ]
    cache_key = _deterministic_cache_key(payload, parser_id, parser_result)
    cached = _read_cached_result(cache_key)
    if cached:
        return cached.model_copy(update={"warnings": [*cached.warnings, "deterministic_cache_hit"]})

    case = await CaseRepository(session).create(
        title=f"Extraction Lab: {input_info.name}",
        metadata_json={
            "synthetic": True,
            "input_id": payload.input_id,
            "parser_run_id": parser_result.run_id,
            "extraction_tier": payload.extraction_tier.value,
        },
    )
    doc = await DocumentRepository(session).create(
        case_id=case.case_id,
        filename=input_info.name,
        mime_type=input_info.input_type,
        size_bytes=input_info.size_bytes,
        user_metadata={"synthetic_extraction_lab": True},
    )
    cleaned = evidence_cleaner.clean_parser_result(parser_result, max_pages=max(payload.max_pages or 200, len(chunks) and max(c.page for c in chunks) or 200))
    logger.info("extraction_lab: evidence_cleaner enabled=%s items=%d", cleaned.get('enabled'), len(cleaned.get('items', [])))
    if cleaned.get("enabled") and cleaned.get("items"):
        from app.services.production_ingestions import _clean_items_to_chunks
        await index_chunks(
            session,
            case.case_id,
            doc.document_id,
            _clean_items_to_chunks(cleaned["items"]),
            embed_openai=True,
            embed_api=False,
            replace_existing=True,
        )
    else:
        await index_chunks(
            session,
            case.case_id,
            doc.document_id,
            [_source_chunk_to_chunk(chunk) for chunk in chunks],
            embed_openai=True,
            embed_api=False,
            replace_existing=True,
        )

    logger.info("extraction_lab: chunks built=%d strategy=%s", len(chunks), chunking_strategy_used)
    search_schema = _schema_with_query(payload.output_schema, payload.natural_language_query)
    engine_result = await run_case_extraction_db(
        session,
        case.case_id,
        ExtractionRequest(
            schema_id=payload.output_schema.name or "extraction_lab",
            output_schema=_lab_schema_to_json_schema(search_schema),
            max_evidence_per_field=payload.max_candidates_per_field,
            settings=payload.settings,
        ),
        agentic=True,
    )
    logger.info("extraction_lab: extraction complete status=%s", engine_result.status)
    fields, data, candidates_scanned = await _lab_fields_from_engine(session, payload.output_schema, engine_result)

    model_name, dynamic_model, generated_code = _build_pydantic_model(payload.output_schema)
    validation_errors = _validate_required(payload.output_schema, data)
    try:
        model_instance = dynamic_model.model_validate(data)
        data = model_instance.model_dump(mode="json", by_alias=True)
    except ValidationError as exc:
        validation_errors.extend(_format_validation_errors(exc))
        data = _json_safe(data)

    error_by_key = _errors_by_key(validation_errors)
    normalized_fields = [
        result.model_copy(
            update={
                "valid": result.valid and result.key not in error_by_key,
                "validation_message": error_by_key.get(result.key) or result.validation_message,
                "value": data.get(result.key),
            }
        )
        for result in fields
    ]

    finished_at = utcnow()
    total_seconds = time.perf_counter() - start
    warnings = _run_warnings(parser_result, chunks, validation_errors)
    warnings.append(f"path_b_job_id:{engine_result.job_id}")
    consistency = _consistency_from_engine(engine_result)
    retrieval_stats = _retrieval_stats_from_engine(engine_result)
    response = ExtractionRunResponse(
        run_id=engine_result.job_id,
        input=input_info,
        parser_id=parser_id,
        parser_name=parser_name,
        parser_run_id=parser_result.run_id or None,
        parser_run_started_at=parser_run_started_at,
        extraction_tier=payload.extraction_tier,
        schema_model_name=model_name,
        schema_definition=payload.output_schema.model_dump(mode="json"),
        natural_language_query=payload.natural_language_query,
        data=data,
        fields=normalized_fields,
        chunks=[_chunk_to_extraction_chunk(chunk) for chunk in chunks[:300]],
        validation_errors=validation_errors,
        warnings=warnings,
        generated_code=generated_code,
        stats=ExtractionRunStats(
            parser_seconds=parser_result.seconds,
            total_seconds=round(total_seconds, 4),
            pages=parser_result.pages,
            chunks=len(chunks),
            fields=len(payload.output_schema.fields),
            candidates_scanned=candidates_scanned,
            chunking_strategy=chunking_strategy_used,
            chunk_tokens=sum(chunk.token_count or 0 for chunk in chunks),
            retrieval_mode=str(retrieval_stats.get("retrieval_mode") or "unknown"),
            dense_hits=int(retrieval_stats.get("dense_hits") or 0),
            sparse_hits=int(retrieval_stats.get("sparse_hits") or 0),
            null_fields_detected=int(consistency.get("null_fields_detected") or 0),
            null_retries=int(consistency.get("null_retries") or 0),
            recovered_nulls=int(consistency.get("recovered_nulls") or 0),
            candidate_conflicts=int(consistency.get("candidate_conflicts") or 0),
            critic_issues=int(consistency.get("critic_issue_count") or 0),
            consistency_score=float(consistency.get("consistency_score") or 1.0),
            agentic_used=True,
            adk_available=bool(consistency.get("adk_available")),
            model_used=consistency.get("model_used") if isinstance(consistency.get("model_used"), str) else None,
        ),
        started_at=started_at,
        finished_at=finished_at,
    )
    response = enrich_response_bboxes(response)
    if not response.validation_errors:
        _write_cached_result(cache_key, response)
    await _save_extraction_result_to_db(session, payload.input_id, response)
    return response


def _has_existing_parser_result(payload: ExtractionRunRequest) -> bool:
    input_info = resolve_input(payload.input_id)
    if not input_info:
        return False
    if input_info.input_type == "text" and payload.parser_id in {"", "auto", "plain_text"}:
        return True
    selected_ids = AUTO_PARSER_ORDER if payload.parser_id in {"", "auto"} else [payload.parser_id]
    for parser_id in selected_ids:
        latest = get_latest_ok_result_for_input(payload.input_id, parser_id)
        if latest:
            return True
    return False


async def run_multi_document_extraction_db(
    session: AsyncSession,
    payload: MultiDocumentExtractionRunRequest,
) -> MultiDocumentExtractionRunResponse:
    """Run batch extraction over multiple documents.

    Supports two modes:
    * `PER_DOCUMENT`: Runs separate standalone extractions for each document concurrently.
    * `CROSS_DOCUMENT` (Bundle): Combines chunks from all documents into a single case,
       allowing the schema-constrained LLM to query and aggregate metrics across multiple files
       simultaneously (e.g. comparing annual reports from consecutive years).
    """
    input_ids = list(dict.fromkeys(payload.input_ids))
    logger.info("extraction_lab: multi_doc mode=%s documents=%d", payload.multi_document_mode, len(input_ids))
    if payload.multi_document_mode == MultiDocumentMode.PER_DOCUMENT:
        # Enforce pre-parsed check for all documents in the batch
        for input_id in input_ids:
            single = _single_payload(payload, input_id)
            if not _has_existing_parser_result(single):
                input_info = resolve_input(input_id)
                fname = input_info.name if input_info else input_id
                raise HTTPException(
                    status_code=400,
                    detail=f"Document '{fname}' has not been parsed yet. Please run parsing first."
                )

        import asyncio
        from app.db.engine import get_factory
        session_creator = get_factory()

        async def run_single(input_id):
            async with session_creator() as local_session:
                return await run_extraction_db(local_session, _single_payload(payload, input_id))

        tasks = [run_single(input_id) for input_id in input_ids]
        results = await asyncio.gather(*tasks)
        return MultiDocumentExtractionRunResponse(mode=payload.multi_document_mode, results=results)

    start = time.perf_counter()
    started_at = utcnow()
    docs: list[tuple[ParserInputInfo, str, str, ParserRunResult, datetime | None, list[SourceChunk]]] = []
    for input_id in input_ids:
        single = _single_payload(payload, input_id)
        input_info = resolve_input(input_id)
        if not input_info:
            raise HTTPException(status_code=404, detail=f"Extraction input not found: {input_id}")
        parser_id, parser_name, parser_result, parser_run_started_at = _load_parser_result(single)
        chunks = _build_chunks(parser_result, payload.max_pages, single)
        if not chunks:
            chunks = [SourceChunk(id="document-1", page=1, type="text", text=parser_result.raw_text or parser_result.text_preview or "")]
        docs.append((input_info, parser_id, parser_name, parser_result, parser_run_started_at, chunks))

    case = await CaseRepository(session).create(
        title=f"Extraction Lab Bundle: {len(docs)} documents",
        metadata_json={"synthetic": True, "input_ids": input_ids, "mode": payload.multi_document_mode},
    )
    all_chunks: list[SourceChunk] = []
    for input_info, _parser_id, _parser_name, _parser_result, _started, chunks in docs:
        doc = await DocumentRepository(session).create(
            case_id=case.case_id,
            filename=input_info.name,
            mime_type=input_info.input_type,
            size_bytes=input_info.size_bytes,
            user_metadata={"synthetic_extraction_lab": True, "input_id": input_info.id},
        )
        prefixed = [_prefix_chunk(chunk, doc.document_id) for chunk in chunks]
        all_chunks.extend(prefixed)
        cleaned = evidence_cleaner.clean_parser_result(_parser_result, max_pages=200)
        if cleaned.get("enabled") and cleaned.get("items"):
            from app.services.production_ingestions import _clean_items_to_chunks
            await index_chunks(
                session,
                case.case_id,
                doc.document_id,
                _clean_items_to_chunks(cleaned["items"]),
                embed_openai=True,
                embed_api=False,
                replace_existing=True,
            )
        else:
            await index_chunks(
                session,
                case.case_id,
                doc.document_id,
                [_source_chunk_to_chunk(chunk) for chunk in prefixed],
                embed_openai=True,
                embed_api=False,
                replace_existing=True,
            )

        logger.info("extraction_lab: indexed document=%s", doc.document_id)

    search_schema = _schema_with_query(payload.output_schema, payload.natural_language_query)
    cache_key = _deterministic_cache_key(
        payload,
        "multi",
        ParserRunResult(
            result_id="multi",
            run_id=",".join(item[3].run_id or "" for item in docs),
            library="multi",
            input_file=",".join(item[0].name for item in docs),
            input_type="bundle",
            status=ParserStatus.OK,
            seconds=0,
            pages=sum(item[3].pages for item in docs),
            chars=sum(item[3].chars for item in docs),
            tables=sum(item[3].tables for item in docs),
            images=sum(item[3].images for item in docs),
            text_preview=",".join(item[3].run_id or "" for item in docs),
        ),
    )
    cached = _read_cached_result(cache_key)
    if cached:
        return MultiDocumentExtractionRunResponse(mode=payload.multi_document_mode, results=[cached.model_copy(update={"warnings": [*cached.warnings, "deterministic_cache_hit"]})])
    engine_result = await run_case_extraction_db(
        session,
        case.case_id,
        ExtractionRequest(
            schema_id=payload.output_schema.name or "extraction_lab_bundle",
            output_schema=_lab_schema_to_json_schema(search_schema),
            max_evidence_per_field=payload.max_candidates_per_field,
            settings=payload.settings,
        ),
        agentic=True,
    )
    fields, data, candidates_scanned = await _lab_fields_from_engine(session, payload.output_schema, engine_result)
    model_name, dynamic_model, generated_code = _build_pydantic_model(payload.output_schema)
    validation_errors = _validate_required(payload.output_schema, data)
    try:
        data = dynamic_model.model_validate(data).model_dump(mode="json", by_alias=True)
    except ValidationError as exc:
        validation_errors.extend(_format_validation_errors(exc))
        data = _json_safe(data)

    consistency = _consistency_from_engine(engine_result)
    retrieval_stats = _retrieval_stats_from_engine(engine_result)
    total_seconds = time.perf_counter() - start
    bundle_input = ParserInputInfo(
        id="bundle:" + ",".join(input_ids),
        name=f"{len(docs)} document bundle",
        input_type="bundle",
        size_bytes=sum(item[0].size_bytes for item in docs),
        path="",
        page_count=sum(item[3].pages for item in docs),
    )
    result = ExtractionRunResponse(
        run_id=engine_result.job_id,
        input=bundle_input,
        parser_id="multi",
        parser_name="Multi-document",
        parser_run_id=None,
        parser_run_started_at=docs[0][4] if docs else None,
        extraction_tier=payload.extraction_tier,
        schema_model_name=model_name,
        schema_definition=payload.output_schema.model_dump(mode="json"),
        natural_language_query=payload.natural_language_query,
        data=data,
        fields=fields,
        chunks=[_chunk_to_extraction_chunk(chunk) for chunk in all_chunks[:300]],
        validation_errors=validation_errors,
        warnings=[f"path_b_job_id:{engine_result.job_id}", f"cross_document_inputs:{len(docs)}"],
        generated_code=generated_code,
        stats=ExtractionRunStats(
            parser_seconds=sum(item[3].seconds for item in docs),
            total_seconds=round(total_seconds, 4),
            pages=sum(item[3].pages for item in docs),
            chunks=len(all_chunks),
            fields=len(payload.output_schema.fields),
            candidates_scanned=candidates_scanned,
            chunking_strategy=payload.chunking_strategy,
            chunk_tokens=sum(chunk.token_count or 0 for chunk in all_chunks),
            retrieval_mode=str(retrieval_stats.get("retrieval_mode") or "unknown"),
            dense_hits=int(retrieval_stats.get("dense_hits") or 0),
            sparse_hits=int(retrieval_stats.get("sparse_hits") or 0),
            null_fields_detected=int(consistency.get("null_fields_detected") or 0),
            null_retries=int(consistency.get("null_retries") or 0),
            recovered_nulls=int(consistency.get("recovered_nulls") or 0),
            candidate_conflicts=int(consistency.get("candidate_conflicts") or 0),
            critic_issues=int(consistency.get("critic_issue_count") or 0),
            consistency_score=float(consistency.get("consistency_score") or 1.0),
            agentic_used=True,
            adk_available=bool(consistency.get("adk_available")),
            model_used=consistency.get("model_used") if isinstance(consistency.get("model_used"), str) else None,
        ),
        started_at=started_at,
        finished_at=utcnow(),
    )
    if not result.validation_errors:
        _write_cached_result(cache_key, result)
    await _save_extraction_result_to_db(session, "bundle:" + ",".join(input_ids), result)
    return MultiDocumentExtractionRunResponse(mode=payload.multi_document_mode, results=[result])


def _single_payload(payload: MultiDocumentExtractionRunRequest, input_id: str) -> ExtractionRunRequest:
    return ExtractionRunRequest(**{**payload.model_dump(mode="python"), "input_id": input_id})


def _deterministic_cache_dir() -> Path:
    path = Path("data") / "extraction_lab_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _deterministic_cache_key(
    payload: ExtractionRunRequest,
    parser_id: str,
    parser_result: ParserRunResult,
) -> str:
    material = {
        "input_id": payload.input_id,
        "parser_id": parser_id,
        "parser_run_id": parser_result.run_id,
        "parser_input_file": parser_result.input_file,
        "parser_pages": parser_result.pages,
        "parser_chars": parser_result.chars,
        "schema": payload.output_schema.model_dump(mode="json"),
        "natural_language_query": payload.natural_language_query,
        "chunking_strategy": payload.chunking_strategy,
        "chunk_size": payload.chunk_size,
        "chunk_overlap": payload.chunk_overlap,
        "max_pages": payload.max_pages,
        "max_candidates_per_field": payload.max_candidates_per_field,
        "extraction_tier": payload.extraction_tier,
    }
    encoded = json.dumps(material, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_cached_result(cache_key: str) -> ExtractionRunResponse | None:
    path = _deterministic_cache_dir() / f"{cache_key}.json"
    if not path.exists():
        return None
    try:
        res = ExtractionRunResponse.model_validate_json(path.read_text(encoding="utf-8"))
        return enrich_response_bboxes(res)
    except Exception:
        return None


def _write_cached_result(cache_key: str, result: ExtractionRunResponse) -> None:
    path = _deterministic_cache_dir() / f"{cache_key}.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")


def _prefix_chunk(chunk: SourceChunk, document_id: str) -> SourceChunk:
    return SourceChunk(**{**chunk.__dict__, "id": f"{document_id}:{chunk.id}"})


def _load_parser_result(payload: ExtractionRunRequest) -> tuple[str, str, ParserRunResult, datetime | None]:
    input_info = resolve_input(payload.input_id)
    assert input_info is not None
    if input_info.input_type == "text" and payload.parser_id in {"", "auto", "plain_text"}:
        return "plain_text", "Plain text", _parse_plain_text(Path(input_info.path), payload.preview_chars), None

    selected_ids = AUTO_PARSER_ORDER if payload.parser_id in {"", "auto"} else [payload.parser_id]
    # When auto-selecting, prefer the main parser (Mistral OCR) alone first if
    # it is installed and supports the input type. Only fall through to the rest
    # of the order if it is unavailable for this input.
    if payload.parser_id in {"", "auto"}:
        main_module = PARSERS.get(MAIN_PARSER_ID)
        if (
            main_module
            and MAIN_PARSER_ID in SUPPORTED_EXTRACTION_PARSERS
            and input_info.input_type in main_module.SUPPORTED_INPUT_TYPES
            and main_module.is_available()
        ):
            selected_ids = [MAIN_PARSER_ID]
    unsupported = [parser_id for parser_id in selected_ids if parser_id not in SUPPORTED_EXTRACTION_PARSERS]
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail=f"Extraction Lab currently supports only: {', '.join(AUTO_PARSER_ORDER)}",
        )

    skipped: list[str] = []
    for parser_id in selected_ids:
        module = PARSERS.get(parser_id)
        if not module:
            logger.info("extraction_lab: parser_skip parser=%s reason=%s", parser_id, "unknown parser")
            skipped.append(f"{parser_id}: unknown parser")
            continue
        if input_info.input_type not in module.SUPPORTED_INPUT_TYPES:
            logger.info("extraction_lab: parser_skip parser=%s reason=%s", parser_id, "unsupported input type")
            skipped.append(f"{parser_id}: unsupported input type")
            continue
        latest = get_latest_ok_result_for_input(payload.input_id, parser_id)
        if latest:
            run, result = latest
            logger.info("extraction_lab: parser_ok parser=%s pages=%d chars=%d", parser_id, result.pages, result.chars)
            return parser_id, module.DISPLAY_NAME, result, run.started_at
        logger.info("extraction_lab: parser_skip parser=%s reason=%s", parser_id, "no latest OK parser output")
        skipped.append(f"{parser_id}: no latest OK parser output")

    try:
        parser_id, parser_name, parsed = _parse_with_best_parser(payload)
        logger.info("extraction_lab: parser_ok parser=%s pages=%d chars=%d", parser_id, parsed.pages, parsed.chars)
        return parser_id, parser_name, parsed, None
    except HTTPException as exc:
        detail = "; ".join(skipped) or "No latest parser output found"
        raise HTTPException(
            status_code=422,
            detail=f"No latest parser output found and automatic parsing failed. {detail}; {exc.detail}",
        ) from exc


def _parse_with_best_parser(payload: ExtractionRunRequest) -> tuple[str, str, ParserRunResult]:
    input_info = resolve_input(payload.input_id)
    assert input_info is not None
    if input_info.input_type == "text" and payload.parser_id in {"", "auto", "plain_text"}:
        return "plain_text", "Plain text", _parse_plain_text(Path(input_info.path), payload.preview_chars)

    selected_ids = (
        AUTO_PARSER_ORDER
        if payload.parser_id in {"", "auto"}
        else [payload.parser_id]
    )
    unknown = [parser_id for parser_id in selected_ids if parser_id not in PARSERS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown parser(s): {', '.join(unknown)}")

    input_path = Path(input_info.path)
    skipped: list[str] = []
    failed: list[str] = []
    logger.info("extraction_lab: parse_with_best_parser input=%s", input_info.name)
    for parser_id in selected_ids:
        module = PARSERS[parser_id]
        if input_info.input_type not in module.SUPPORTED_INPUT_TYPES:
            skipped.append(f"{parser_id}: unsupported input type")
            continue
        if not module.is_available():
            skipped.append(f"{parser_id}: not installed")
            continue
        try:
            result = module.parse(input_path, preview_chars=payload.preview_chars)
        except Exception as e:
            logger.warning("extraction_lab: parser_fail parser=%s: %s", parser_id, e)
            failed.append(f"{parser_id}: {e.__class__.__name__}: {e}")
            continue
        if result.status == ParserStatus.OK and (result.raw_text or result.text_preview).strip():
            return parser_id, module.DISPLAY_NAME, result
        if result.status == ParserStatus.SKIPPED:
            skipped.append(f"{parser_id}: {result.error or 'skipped'}")
        else:
            failed.append(f"{parser_id}: {result.error or 'empty parser result'}")

    logger.error("extraction_lab: all_parsers_failed")
    detail = "; ".join(failed or skipped or ["No compatible parser could process this input"])
    raise HTTPException(status_code=422, detail=detail)


def _parse_plain_text(input_path: Path, preview_chars: int) -> ParserRunResult:
    start = time.perf_counter()
    text = input_path.read_text(encoding="utf-8", errors="replace")
    pages = [part.strip() for part in re.split(r"\f|\n\s*---+\s*page\s*---+\s*\n", text, flags=re.I) if part.strip()]
    if not pages:
        pages = [text]
    markdown = "\n\n".join(f"<!-- page: {index} -->\n{page}" for index, page in enumerate(pages, start=1))
    return ok_result(
        "plain_text",
        input_path,
        time.perf_counter() - start,
        markdown,
        pages=len(pages),
        preview_chars=preview_chars,
        structured_preview={
            "reading_order": "plain_text",
            "pages": [
                {
                    "page": index,
                    "blocks": 1,
                    "text_preview": preview_text(page, min(preview_chars, 2000)),
                }
                for index, page in enumerate(pages, start=1)
            ],
        },
    )


def _build_chunks(result: ParserRunResult, max_pages: int, payload: ExtractionRunRequest | None = None) -> list[SourceChunk]:
    strategy = _normalize_chunk_strategy(payload.chunking_strategy if payload else DEFAULT_CHUNK_STRATEGY)
    config = ChunkConfig(
        max_pages=max_pages,
        chunk_size=payload.chunk_size if payload else 500,
        chunk_overlap=payload.chunk_overlap if payload else 80,
    )
    chunks = chunk_parser_result(result, strategy=strategy, config=config)
    source_chunks = [_chunk_to_source_chunk(chunk) for chunk in chunks]
    return [chunk for chunk in source_chunks if chunk]


def _normalize_chunk_strategy(value: str | None) -> ChunkStrategy:
    if not value:
        return DEFAULT_CHUNK_STRATEGY
    normalized = re.sub(r"_+", "_", value.strip().lower().replace("-", "_").replace("by", "")).strip("_")
    aliases = {
        "page_page": "page",
        "page": "page",
        "fixed_size": "sliding_window",
        "fixed": "sliding_window",
        "window": "sliding_window",
        "semantic": "block",
        "row": "table_row",
        "rows": "table_row",
        "per_page": "page",
        "per_document": "document",
        "table_row": "table_row",
        "sliding_window": "sliding_window",
        "document": "document",
        "block": "block",
    }
    normalized = aliases.get(normalized, normalized)
    valid = {"document", "page", "table_row", "sliding_window", "block"}
    return normalized if normalized in valid else DEFAULT_CHUNK_STRATEGY  # type: ignore[return-value]


def _chunk_to_source_chunk(chunk: Chunk) -> SourceChunk | None:
    text = chunk.text
    if (not text or not text.strip()) and chunk.source_url:
        text = f"Image evidence: {chunk.source_url}"
    if not text or not text.strip():
        return None
    return SourceChunk(
        id=chunk.chunk_id,
        page=chunk.page,
        type=chunk.chunk_type,
        text=text,
        bbox=chunk.bbox,
        confidence=chunk.confidence,
        risk=chunk.risk,
        warnings=list(chunk.warnings),
        source_url=chunk.source_url,
        columns=list(chunk.columns) if chunk.columns else None,
        rows=list(chunk.rows) if chunk.rows else None,
        strategy=chunk.strategy,
        table_index=chunk.table_index,
        row_index=chunk.row_index,
        header=list(chunk.header) if chunk.header else None,
        token_count=chunk.token_count,
    )


def _chunk_to_extraction_chunk(chunk: SourceChunk) -> ExtractionChunk:
    return ExtractionChunk(
        id=chunk.id,
        page=chunk.page,
        type=chunk.type,
        char_count=len(chunk.text),
        text_preview=preview_text(chunk.text, 420),
        bbox=chunk.bbox,
        confidence=chunk.confidence,
        risk=chunk.risk,
        warnings=chunk.warnings or [],
        source_url=chunk.source_url,
        columns=chunk.columns or [],
        rows=chunk.rows or [],
        strategy=chunk.strategy,
        table_index=chunk.table_index,
        row_index=chunk.row_index,
        header=chunk.header,
        token_count=chunk.token_count,
    )


def _source_chunk_to_chunk(chunk: SourceChunk) -> Chunk:
    return Chunk(
        chunk_id=chunk.id,
        page=chunk.page,
        chunk_type=chunk.type,
        text=chunk.text,
        bbox=chunk.bbox,
        confidence=chunk.confidence,
        risk=chunk.risk,
        warnings=chunk.warnings or [],
        source_url=chunk.source_url,
        columns=chunk.columns,
        rows=chunk.rows,
        table_index=chunk.table_index,
        row_index=chunk.row_index,
        header=chunk.header,
        token_count=chunk.token_count,
        strategy=chunk.strategy,
    )


def _lab_schema_to_json_schema(schema: ExtractionLabSchema) -> dict[str, Any]:
    properties = {field.key: _lab_field_to_json_schema(field) for field in schema.fields}
    return {
        "type": "object",
        "properties": properties,
        "required": [field.key for field in schema.fields if field.required],
    }


def _lab_field_to_json_schema(field: ExtractionSchemaField) -> dict[str, Any]:
    type_map = {
        ExtractionFieldType.NUMBER: "number",
        ExtractionFieldType.CURRENCY: "number",
        ExtractionFieldType.BOOLEAN: "boolean",
        ExtractionFieldType.LIST: "array",
        ExtractionFieldType.TABLE: "array",
        ExtractionFieldType.OBJECT: "object",
    }
    payload: dict[str, Any] = {
        "type": type_map.get(field.type, "string"),
        "description": _enhanced_field_description(field.key, field.label, field.description),
    }
    if field.children and field.type == ExtractionFieldType.LIST:
        payload["items"] = {
            "type": "object",
            "properties": {child.key: _lab_field_to_json_schema(child) for child in field.children},
            "required": [child.key for child in field.children if child.required],
        }
    elif field.children:
        payload["properties"] = {child.key: _lab_field_to_json_schema(child) for child in field.children}
    return payload


def _enhanced_field_description(key: str, label: str | None, description: str | None) -> str:
    base = "\n".join(part for part in [label, description] if part).strip()
    haystack = f"{key} {label or ''} {description or ''}".lower()
    guidance = ""
    if any(token in haystack for token in ("documenttitle", "document title", "documentname", "document name", "report title")):
        guidance = "Extract the report/document title from the cover or first page; do not use appendix headings such as CREDIT RATING DEFINITIONS."
    elif any(token in haystack for token in ("reportdate", "report date", "reportingperiod", "reporting period")):
        guidance = "Extract the report date or reporting period from the cover/header; prefer month-year or ISO date when clear."
    elif any(token in haystack for token in ("issuer", "companyname", "company name", "ratedentity", "rated entity")):
        guidance = "Extract the rated company/issuer/entity name; do not return the rating agency name."
    elif "ratingdriver" in haystack or "rating driver" in haystack:
        guidance = "Extract concise rating drivers/rationale bullets only; exclude raw tables, definitions, and unrelated narrative."
    elif any(token in haystack for token in ("ratings", "creditratings", "credit ratings", "instrument")):
        guidance = "Extract actual credit ratings and rated instruments/facilities only; exclude headings and unrelated ratio/risk discussion."
    elif "summary" in haystack:
        guidance = "Extract a short user-readable summary supported by the document; avoid raw markup and long copied sections."
    elif any(token in haystack for token in ("image", "figure", "chart", "logo", "visual")):
        guidance = "Extract relevant image or figure references only when the parser evidence contains image URLs; return the image URL and a short caption when possible."
    elif "analyst" in haystack:
        guidance = "Extract analyst/contact person names only when present; exclude cover headings and report titles."
    elif "subsidiar" in haystack or "associate" in haystack:
        guidance = "Extract subsidiary or associate names only; exclude financial tables and cover headings."
    return "\n".join(part for part in [base, guidance] if part)


async def _lab_fields_from_engine(
    session: AsyncSession,
    schema: ExtractionLabSchema,
    engine_result: ExtractionResult,
) -> tuple[list[ExtractionFieldResult], dict[str, Any], int]:
    evidence = await _evidence_for_engine_result(session, engine_result)
    fields: list[ExtractionFieldResult] = []
    data: dict[str, Any] = {}
    candidates_scanned = 0
    for lab_field in schema.fields:
        engine_field = engine_result.fields.get(lab_field.key)
        value = engine_field.value if engine_field else None
        data[lab_field.key] = value
        validation_errors = engine_field.validation_errors if engine_field else []
        candidates = engine_field.candidates if engine_field else []
        candidates_scanned += len(candidates)
        evidence_ids = [evidence_id for candidate in candidates for evidence_id in [item.evidence_id for item in candidate.evidence]]
        field_evidence = [evidence[evidence_id] for evidence_id in dict.fromkeys(evidence_ids) if evidence_id in evidence]
        status = engine_field.status if engine_field else "missing"
        fields.append(
            ExtractionFieldResult(
                key=lab_field.key,
                label=lab_field.label or lab_field.key,
                type=lab_field.type,
                required=lab_field.required,
                value=value,
                raw_value=value,
                confidence=round(engine_field.confidence if engine_field else 0.0, 3),
                valid=status in {"validated", "low_confidence"} and not validation_errors,
                validation_message="; ".join(validation_errors) if validation_errors else (status if status not in {"validated", "low_confidence"} else None),
                evidence=field_evidence[:5],
            )
        )
    return fields, data, candidates_scanned


def _consistency_from_engine(engine_result: ExtractionResult) -> dict[str, Any]:
    report = engine_result.validation_report if isinstance(engine_result.validation_report, dict) else {}
    consistency = report.get("consistency")
    return consistency if isinstance(consistency, dict) else {}


def _retrieval_stats_from_engine(engine_result: ExtractionResult) -> dict[str, Any]:
    report = engine_result.validation_report if isinstance(engine_result.validation_report, dict) else {}
    retrieval_stats = report.get("retrieval_stats")
    return retrieval_stats if isinstance(retrieval_stats, dict) else {}


async def _evidence_for_engine_result(
    session: AsyncSession,
    engine_result: ExtractionResult,
) -> dict[str, ExtractionEvidence]:
    ids = {
        item.evidence_id
        for field in engine_result.fields.values()
        for candidate in field.candidates
        for item in candidate.evidence
        if item.evidence_id
    }
    if not ids:
        return {}
    result = await session.execute(select(EvidenceItemModel).where(EvidenceItemModel.evidence_id.in_(ids)))
    return {
        row.evidence_id: ExtractionEvidence(
            chunk_id=str((row.metadata_json or {}).get("chunk_id") or row.evidence_id),
            page=row.page_number,
            type=row.source_type,
            text_preview=preview_text(row.text or row.markdown or "", 260),
            bbox=row.bbox if isinstance(row.bbox, dict) else None,
            source_url=_evidence_source_url(row),
        )
        for row in result.scalars().all()
    }


def _evidence_source_url(row: EvidenceItemModel) -> str | None:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    direct = metadata.get("source_url") or metadata.get("url")
    if isinstance(direct, str) and direct:
        return direct
    text = str(row.markdown or row.text or "")
    match = re.search(r"!\[[^\]]*]\(([^)]+)\)|(/api/parser-benchmarks/media/\S+)", text)
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip() or None


def _schema_with_query(schema: ExtractionLabSchema, query: str | None) -> ExtractionLabSchema:
    clean_query = (query or "").strip()
    if not clean_query:
        return schema
    fields = []
    for field in schema.fields:
        extra = f"User request: {clean_query}"
        description = f"{field.description or ''}\n{extra}".strip()
        fields.append(field.model_copy(update={"description": description}))
    return schema.model_copy(update={"description": clean_query, "fields": fields})


def enrich_response_bboxes(response: ExtractionRunResponse) -> ExtractionRunResponse:
    """No-op. Bbox coordinate mapping search logic is disabled to keep the pipeline simple."""
    return response


def _append_missed_fields_table(report_text: str, result: ExtractionRunResponse) -> str:
    missed = []
    for field in result.fields:
        is_missing = field.value in (None, "", [], {})
        is_invalid = not field.valid
        if is_missing or is_invalid:
            status = "Missing" if is_missing else "Invalid"
            reason = field.validation_message or "Value not found in document"
            m_type = field.type.value if hasattr(field.type, "value") else str(field.type)
            missed.append({
                "label": field.label or field.key,
                "key": field.key,
                "type": m_type,
                "required": "Yes" if getattr(field, "required", False) else "No",
                "status": status,
                "reason": reason
            })
            
    table_lines = [report_text.rstrip(), "", "## Missed fields", ""]
    if missed:
        table_lines.extend([
            "| Field | Key | Type | Required | Status | Reason / Issue |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |"
        ])
        for m in missed:
            table_lines.append(
                f"| {m['label']} | `{m['key']}` | {m['type']} | {m['required']} | {m['status']} | {m['reason']} |"
            )
    else:
        table_lines.append("All fields were successfully extracted with no validation issues.")
        
    return "\n".join(table_lines) + "\n"


def generate_polished_report(result: ExtractionRunResponse) -> str:
    api_key = _openai_api_key()
    if not api_key:
        raise HTTPException(
            status_code=422,
            detail="OpenAI report formatting requires OPENAI_API_KEY. Raw fields and Excel output are still available.",
        )
    report = _call_openai_report(api_key, result)
    return _append_missed_fields_table(report, result)


def _call_openai_report(api_key: str, result: ExtractionRunResponse) -> str:
    field_payload = [
        {
            "key": field.key,
            "label": field.label,
            "type": field.type,
            "value": field.value,
            "confidence": field.confidence,
            "valid": field.valid,
            "evidence": [
                {
                    "chunk_id": item.chunk_id,
                    "page": item.page,
                    "type": item.type,
                    "preview": item.text_preview,
                }
                for item in field.evidence[:3]
            ],
        }
        for field in result.fields
    ]
    evidence_payload = [
        {
            "id": chunk.id,
            "page": chunk.page,
            "type": chunk.type,
            "preview": chunk.text_preview,
            "columns": chunk.columns[:12],
            "rows": chunk.rows[:8],
            "source_url": chunk.source_url,
            "risk": chunk.risk,
            "warnings": chunk.warnings[:4],
        }
        for chunk in result.chunks[:30]
    ]
    prompt = {
        "schema": result.schema_definition,
        "natural_language_query": result.natural_language_query,
        "fields": field_payload,
        "evidence": evidence_payload,
        "validation_errors": [error.model_dump(mode="json") for error in result.validation_errors],
        "instructions": (
            "Write a concise human-readable extraction report in Markdown. "
            "Use only the supplied extracted fields and evidence. "
            "Keep schema field names visible, cite page/chunk/type inline where useful, "
            "summarize tables without dropping important rows, and call out missing or low-confidence values. "
            "Do not invent unsupported facts."
        ),
    }
    body = json.dumps(
        {
            "model": OPENAI_RECONSTRUCTION_MODEL,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": json.dumps(prompt, ensure_ascii=False)}],
                }
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90, context=_openai_ssl_context()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise HTTPException(status_code=502, detail=f"OpenAI report formatting failed: {detail}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI report formatting failed: {exc}") from exc

    text = _extract_response_text(payload).strip()
    if not text:
        raise HTTPException(status_code=502, detail="OpenAI report formatting returned an empty response")
    return text


def _call_openai_schema_generator(
    api_key: str,
    query: str,
    evidence: list[dict[str, Any]],
    mode: MultiDocumentMode,
) -> ExtractionLabSchema:
    prompt = {
        "natural_language_request": query,
        "document_mode": mode,
        "documents": evidence[:60],
        "allowed_output_shape": {
            "name": "PascalCase model name",
            "description": "short schema purpose",
            "fields": [
                {
                    "key": "camelCase or snake_case property key",
                    "label": "Human label",
                    "type": "text | number | boolean | object | list",
                    "description": "what to extract and any formatting rules",
                    "required": True,
                    "children": [],
                }
            ],
        },
        "type_rules": (
            "Use only these field types: text for STR/string, number for NUM/number, boolean, "
            "object for OBJ/object, and list for arrays. For arrays of objects, use type=list "
            "with children describing the object properties. For arrays of strings, numbers, "
            "or booleans, use type=list with no children and state the item type in description. "
            "Do not use date, currency, email, phone, table, integer, enum, anyOf, or JSON Schema-only keys."
        ),
        "instructions": SCHEMA_GENERATION_SYSTEM_PROMPT,
    }
    body = json.dumps(
        {
            "model": OPENAI_SCHEMA_MODEL,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": json.dumps(prompt, ensure_ascii=False)}],
                }
            ],
            "text": {"format": {"type": "json_object"}},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120, context=_openai_ssl_context()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise HTTPException(status_code=502, detail=f"OpenAI schema generation failed: {detail}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI schema generation failed: {exc}") from exc

    parsed = _extract_response_json(payload)
    schema_payload = parsed.get("schema") if isinstance(parsed.get("schema"), dict) else parsed
    return _normalize_generated_schema(schema_payload, query, evidence)


def _call_openai_field_extractor(
    field: ExtractionSchemaField,
    candidate_chunks: list[SourceChunk],
) -> Optional[tuple[Any, float, str, Optional[str]]]:
    """Extract a single field value with the OpenAI LLM.

    Sends the field definition plus its top candidate chunks (clean Mistral
    markdown) to the LLM and asks for strict JSON. Returns
    ``(value, confidence, extraction_method, evidence_id)`` or ``None`` when the
    LLM is unavailable / declines (no key, network error, unsupported).

    Never raises: the deterministic extractor is the fallback when this returns
    ``None``.
    """
    api_key = _openai_api_key()
    if not api_key:
        return None
    # Trim each chunk so the prompt stays well under the model context window.
    evidence = [
        {
            "id": chunk.id,
            "page": chunk.page,
            "type": chunk.type,
            "text": preview_text(chunk.text, 2000),
            "columns": chunk.columns or [],
            "rows": chunk.rows or [],
        }
        for chunk in candidate_chunks
        if (chunk.text or "").strip()
    ]
    if not evidence:
        return None

    type_hint = field.type.value if hasattr(field.type, "value") else str(field.type)
    field_payload = {
        "key": field.key,
        "label": field.label,
        "type": type_hint,
        "description": field.description or field.label or field.key,
        "required": bool(field.required),
    }
    if field.children:
        field_payload["children"] = [
            {
                "key": child.key,
                "label": child.label,
                "type": child.type.value if hasattr(child.type, "value") else str(child.type),
                "description": child.description or child.label or child.key,
                "required": bool(child.required),
            }
            for child in field.children
        ]
    instructions = SINGLE_FIELD_LLM_PROMPT
    if field.type == ExtractionFieldType.TABLE:
        instructions += (
            "\nFor table fields, return a JSON array of objects representing the rows of the table "
            "(e.g., [{\"column1\": \"value1\", \"column2\": \"value2\"}]). Do not return a single flat object."
        )
    elif field.children:
        child_keys = ", ".join(child.key for child in field.children)
        instructions += f"\nFor this list, extract objects containing the properties: {child_keys}."

    prompt = {
        "field": field_payload,
        "evidence": evidence,
        "instructions": instructions,
    }
    body = json.dumps(
        {
            "model": OPENAI_RECONSTRUCTION_MODEL,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": json.dumps(prompt, ensure_ascii=False)}],
                }
            ],
            "text": {"format": {"type": "json_object"}},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60, context=_openai_ssl_context()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        # Network/HTTP/auth failure → fall back to deterministic extraction.
        return None

    parsed = _extract_response_json(payload)
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("value")
    # Treat absent values as "not found".
    if _missing(value):
        return None
    confidence = parsed.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else 0.85
    except (TypeError, ValueError):
        confidence = 0.85
    confidence = max(0.0, min(1.0, confidence))
    evidence_id = parsed.get("evidence_id")
    if not isinstance(evidence_id, str):
        evidence_id = None
    return value, confidence, "llm_text", evidence_id


def _normalize_generated_schema(payload: Any, query: str, evidence: list[dict[str, Any]] | None = None) -> ExtractionLabSchema:
    if not isinstance(payload, dict):
        payload = {}
    raw_fields = payload.get("fields")
    fields = _normalize_generated_fields(raw_fields if isinstance(raw_fields, list) else [])
    if not fields:
        fields = _fallback_schema_from_context(query, evidence or []).fields
    name = _model_name(str(payload.get("name") or _schema_name_from_query(query) or "GeneratedExtraction"))
    description = str(payload.get("description") or query or "AI generated extraction schema").strip()
    return ExtractionLabSchema(name=name, description=description, fields=fields)


def _normalize_generated_fields(items: list[Any], depth: int = 0) -> list[ExtractionSchemaField]:
    fields: list[ExtractionSchemaField] = []
    used: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_label = str(item.get("label") or item.get("name") or item.get("key") or "Field").strip()
        raw_key = str(item.get("key") or raw_label).strip()
        key = _unique_generated_key(_camel_key(raw_key), used)
        field_type = _normalize_generated_type(item.get("type") or item.get("data_type") or item.get("field_type"))
        children = _normalize_generated_fields(item.get("children") if isinstance(item.get("children"), list) else [], depth + 1)
        if depth >= 4:
            children = []
        if children and field_type not in {ExtractionFieldType.OBJECT, ExtractionFieldType.LIST}:
            field_type = ExtractionFieldType.OBJECT
        fields.append(
            ExtractionSchemaField(
                id=f"ai-{uuid.uuid4().hex[:8]}",
                key=key,
                label=raw_label or key,
                type=field_type,
                description=_enhanced_field_description(key, raw_label, str(item.get("description") or raw_label or key).strip()),
                required=bool(item.get("required", True)),
                children=children,
            )
        )
        if len(fields) >= 80:
            break
    return fields


def _normalize_generated_type(value: Any) -> ExtractionFieldType:
    normalized = str(value or "").strip().lower().replace("[]", "").replace("[", "").replace("]", "")
    aliases = {
        "str": ExtractionFieldType.TEXT,
        "string": ExtractionFieldType.TEXT,
        "text": ExtractionFieldType.TEXT,
        "num": ExtractionFieldType.NUMBER,
        "number": ExtractionFieldType.NUMBER,
        "float": ExtractionFieldType.NUMBER,
        "integer": ExtractionFieldType.NUMBER,
        "int": ExtractionFieldType.NUMBER,
        "bool": ExtractionFieldType.BOOLEAN,
        "boolean": ExtractionFieldType.BOOLEAN,
        "obj": ExtractionFieldType.OBJECT,
        "object": ExtractionFieldType.OBJECT,
        "array": ExtractionFieldType.LIST,
        "list": ExtractionFieldType.LIST,
    }
    field_type = aliases.get(normalized, ExtractionFieldType.TEXT)
    return field_type if field_type in ALLOWED_GENERATED_FIELD_TYPES else ExtractionFieldType.TEXT


def _fallback_schema_from_context(query: str, evidence: list[dict[str, Any]]) -> ExtractionLabSchema:
    fields: list[ExtractionSchemaField] = []
    evidence_text = "\n".join(str(item.get("text") or "") for item in evidence)
    lower = evidence_text.lower()
    if "rating" in lower or "driver" in lower:
        fields.append(_generated_field("ratingDrivers", "Rating Drivers", ExtractionFieldType.LIST, "Main rating drivers or rationale items."))
    if "financial" in lower or "position" in lower:
        fields.append(
            _generated_field(
                "financialPosition",
                "Financial Position",
                ExtractionFieldType.OBJECT,
                "Summary of financial position, capital, liquidity, and performance.",
                children=[
                    _generated_field("summary", "Summary", ExtractionFieldType.TEXT, "Narrative financial position summary."),
                    _generated_field("keyMetrics", "Key Metrics", ExtractionFieldType.LIST, "Array of financial metric names and values."),
                ],
            )
        )
    if "stakeholder" in lower or "shareholder" in lower:
        fields.append(
            _generated_field(
                "stakeholders",
                "Stakeholders",
                ExtractionFieldType.LIST,
                "Array of major stakeholders or shareholders.",
                children=[
                    _generated_field("name", "Name", ExtractionFieldType.TEXT, "Stakeholder name."),
                    _generated_field("type", "Type", ExtractionFieldType.TEXT, "Stakeholder type or relationship."),
                    _generated_field("ownershipPercentage", "Ownership Percentage", ExtractionFieldType.NUMBER, "Ownership percentage when available."),
                ],
            )
        )
    if not fields and evidence:
        fields.append(_generated_field("documentSummary", "Document Summary", ExtractionFieldType.TEXT, "Concise summary of the selected parser evidence."))
    if not fields and not evidence:
        keywords = [token for token in re.split(r"[^A-Za-z0-9]+", query) if len(token) > 2 and token.lower() not in STOPWORDS]
        for token in keywords[:8]:
            label = re.sub(r"(?<!^)([A-Z])", r" \1", token).strip().title()
            fields.append(_generated_field(_camel_key(token), label, ExtractionFieldType.TEXT, f"{label} extracted from the selected source."))
    if not fields:
        fields.append(_generated_field("summary", "Summary", ExtractionFieldType.TEXT, "Requested extracted information."))
    return ExtractionLabSchema(
        name=_schema_name_from_query(query) or "GeneratedExtraction",
        description=query or "Generated extraction schema",
        fields=fields,
    )


def _generated_field(
    key: str,
    label: str,
    field_type: ExtractionFieldType,
    description: str,
    children: list[ExtractionSchemaField] | None = None,
) -> ExtractionSchemaField:
    return ExtractionSchemaField(
        id=f"ai-{uuid.uuid4().hex[:8]}",
        key=key,
        label=label,
        type=field_type,
        description=description,
        required=True,
        children=children or [],
    )


def _schema_name_from_query(query: str) -> str:
    tokens = [token for token in re.split(r"[^A-Za-z0-9]+", query) if token and token.lower() not in STOPWORDS]
    if not tokens:
        return "GeneratedExtraction"
    return _model_name("".join(token[:1].upper() + token[1:] for token in tokens[:4]))


def _camel_key(value: str) -> str:
    words = [word for word in re.split(r"[^A-Za-z0-9]+", value) if word]
    if not words:
        return "field"
    first = words[0][:1].lower() + words[0][1:]
    return first + "".join(word[:1].upper() + word[1:] for word in words[1:])


def _unique_generated_key(key: str, used: set[str]) -> str:
    base = key or "field"
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}{index}"
        index += 1
    used.add(candidate)
    return candidate


def _extract_response_json(payload: dict[str, Any]) -> dict[str, Any]:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return json.loads(output_text)
    for item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                return json.loads(content["text"])
    return {}


def _extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    parts: list[str] = []
    for item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "\n\n".join(parts)


def _openai_api_key() -> str:
    return runtime_env_value("OPENAI_API_KEY")


def _openai_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _extract_field(
    field: ExtractionSchemaField,
    chunks: list[SourceChunk],
    max_candidates: int,
) -> tuple[ExtractionFieldResult, int]:
    candidates = _candidate_chunks(field, chunks, max_candidates)
    selected_chunks = [candidate.chunk for candidate in candidates]
    if not selected_chunks:
        wants_images = _field_wants_images(field)
        if wants_images:
            selected_chunks = chunks[:max_candidates]
        else:
            selected_chunks = [c for c in chunks if c.type != "image" and "Image evidence:" not in c.text][:max_candidates]

    if field.type == ExtractionFieldType.OBJECT and field.children:
        raw: dict[str, Any] = {}
        evidence: list[ExtractionEvidence] = []
        confidence_values: list[float] = []
        for child in field.children:
            child_result, _ = _extract_field(child, chunks, max_candidates)
            raw[child.key] = child_result.value
            evidence.extend(child_result.evidence[:2])
            confidence_values.append(child_result.confidence)
        value = raw
        confidence = round(sum(confidence_values) / max(len(confidence_values), 1), 3)
        return _field_result(field, value, value, confidence, evidence), len(selected_chunks)

    raw_value, source_chunk, confidence = _extract_field_value_llm_first(
        field, selected_chunks, chunks
    )
    value = _coerce_value(raw_value, field.type)
    if field.type == ExtractionFieldType.TABLE and isinstance(value, list) and source_chunk:
        for row in value:
            if isinstance(row, dict):
                row.setdefault("_evidence_page", str(source_chunk.page))
                row.setdefault("_evidence_chunk", source_chunk.id)
    evidence = [_evidence(source_chunk)] if source_chunk else []
    if field.required and _missing(value):
        confidence = 0.0
    return _field_result(field, value, raw_value, confidence, evidence), len(selected_chunks)


def _extract_field_value_llm_first(
    field: ExtractionSchemaField,
    selected_chunks: list[SourceChunk],
    all_chunks: list[SourceChunk],
) -> tuple[Any, Optional[SourceChunk], float]:
    """LLM-first extractor for a leaf field.

    Always asks the OpenAI LLM to read the candidate chunks and return the
    field value. Falls back to the deterministic extractor
    (``_extract_raw_value``) only when the LLM is unavailable or declines, so
    extraction still works offline / without an API key.

    Returns ``(raw_value, evidence_chunk, confidence)``.
    """
    if _field_wants_images(field):
        return _extract_raw_value(field, selected_chunks, all_chunks)

    # 1. LLM extraction (primary path).
    llm = _call_openai_field_extractor(field, selected_chunks)
    if llm is not None:
        value, confidence, _method, evidence_id = llm
        evidence_chunk: Optional[SourceChunk] = None
        for chunk in selected_chunks:
            if chunk.id == evidence_id:
                evidence_chunk = chunk
                break
        if evidence_chunk is None and selected_chunks:
            evidence_chunk = selected_chunks[0]
        return value, evidence_chunk, confidence

    # 2. Deterministic fallback (network failure / no key / null LLM answer).
    return _extract_raw_value(field, selected_chunks, all_chunks)


def _candidate_chunks(
    field: ExtractionSchemaField,
    chunks: list[SourceChunk],
    max_candidates: int,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    field_tokens = _field_tokens(field)
    label_norms = _field_label_norms(field)
    pattern = TYPE_PATTERNS.get(field.type)
    wants_images = _field_wants_images(field)
    for chunk in chunks:
        if not wants_images and (chunk.type == "image" or "Image evidence:" in chunk.text):
            continue
        text_lower = chunk.text.lower()
        tokens = _tokens(chunk.text)
        score = 0.0
        if chunk.type == "table" and field.type == ExtractionFieldType.TABLE:
            score += 8
        for label in label_norms:
            if label and label in _normalize_text(text_lower):
                score += 10
        overlap = field_tokens.intersection(tokens)
        score += min(len(overlap), 6) * 1.25
        if pattern and pattern.search(chunk.text):
            score += 2.5
        if score > 0:
            candidates.append(Candidate(chunk=chunk, score=score))
    candidates.sort(key=lambda candidate: (candidate.score, -candidate.chunk.page), reverse=True)
    return candidates[:max_candidates]


def _extract_raw_value(
    field: ExtractionSchemaField,
    candidates: list[SourceChunk],
    all_chunks: list[SourceChunk],
) -> tuple[Any, Optional[SourceChunk], float]:
    if field.type == ExtractionFieldType.TABLE:
        rows, source = _extract_tables(candidates) or _extract_tables(all_chunks) or ([], None)
        return rows, source, 0.86 if rows else 0.0
    if field.type == ExtractionFieldType.LIST:
        if field.children:
            rows, source = _extract_tables(candidates) or _extract_tables(all_chunks) or ([], None)
            if rows:
                return rows, source, 0.82
            items, source = _extract_list(field, candidates or all_chunks)
            first_child = field.children[0].key if field.children else "value"
            return [{first_child: item} for item in items], source, 0.72 if items else 0.0
        items, source = _extract_list(field, candidates or all_chunks)
        return items, source, 0.78 if items else 0.0

    for chunk in candidates:
        labeled = _extract_labeled_value(field, chunk.text)
        if labeled is not None:
            return labeled, chunk, 0.9

    pattern = TYPE_PATTERNS.get(field.type)
    if pattern:
        for chunk in candidates:
            match = pattern.search(chunk.text)
            if match:
                return match.group(0), chunk, 0.74

    if field.type == ExtractionFieldType.TEXT:
        for chunk in candidates:
            line = _best_text_line(field, chunk.text)
            if line:
                return line, chunk, 0.66

    return None, None, 0.0


def _extract_labeled_value(field: ExtractionSchemaField, text: str) -> Optional[str]:
    labels = [value for value in [field.label, field.key, field.description or ""] if value]
    for label in labels:
        escaped = re.escape(label.replace("_", " "))
        match = re.search(
            rf"(?im)^\s*{escaped}\s*(?:[:#=|-]|\s{{2,}})\s*(?P<value>[^\n\r]{{1,260}})",
            text,
        )
        if match:
            return _trim_value(match.group("value"))
    for line in text.splitlines():
        line_norm = _normalize_text(line)
        if any(_normalize_text(label) in line_norm for label in labels):
            after = re.split(r"[:#=|-]", line, maxsplit=1)
            if len(after) == 2 and after[1].strip():
                return _trim_value(after[1])
    return None


def _best_text_line(field: ExtractionSchemaField, text: str) -> Optional[str]:
    field_tokens = _field_tokens(field)
    best: tuple[int, str] | None = None
    for line in text.splitlines():
        clean = line.strip(" |")
        if len(clean) < 2:
            continue
        score = len(field_tokens.intersection(_tokens(clean)))
        if score and (best is None or score > best[0]):
            best = (score, clean)
    if best:
        return _trim_value(best[1])
    first_line = next((line.strip(" |#") for line in text.splitlines() if _usable_extraction_line(line)), "")
    return preview_text(first_line, 260) if first_line else None


def _usable_extraction_line(line: str) -> bool:
    clean = line.strip(" |#")
    if len(clean) < 2:
        return False
    if clean.startswith("![") or clean.startswith("!![") or "/api/parser-benchmarks/media/" in clean:
        return False
    return True


def _extract_list(
    field: ExtractionSchemaField,
    candidates: list[SourceChunk],
) -> tuple[list[str], Optional[SourceChunk]]:
    if _field_wants_images(field):
        image_items: list[str] = []
        source: Optional[SourceChunk] = None
        for chunk in candidates:
            if chunk.type != "image" and not chunk.source_url:
                continue
            source = source or chunk
            label = chunk.text.splitlines()[0].strip() if chunk.text.strip() else "image"
            if chunk.source_url:
                image_items.append(f"{label} ({chunk.source_url})")
            else:
                image_items.append(label)
            if len(image_items) >= 20:
                break
        return image_items, source

    for chunk in candidates:
        labeled = _extract_labeled_value(field, chunk.text)
        if labeled:
            parts = [part.strip(" -;") for part in re.split(r"[,;\n]", labeled) if part.strip(" -;")]
            if parts:
                return parts, chunk
        bullets = [
            re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
            for line in chunk.text.splitlines()
            if re.match(r"^\s*(?:[-*]|\d+[.)])\s+\S+", line)
        ]
        if bullets:
            return bullets[:30], chunk
    return [], None


def _extract_tables(chunks: list[SourceChunk]) -> Optional[tuple[list[dict[str, Any]], SourceChunk]]:
    merged_rows: list[dict[str, Any]] = []
    first_source: Optional[SourceChunk] = None
    for chunk in chunks:
        rows: list[dict[str, Any]] = []
        if chunk.rows:
            rows = chunk.rows
        else:
            rows = _markdown_tables(chunk.text)
        if not rows:
            continue
        first_source = first_source or chunk
        for row in rows[:80]:
            annotated = dict(row)
            annotated.setdefault("_evidence_page", str(chunk.page))
            annotated.setdefault("_evidence_chunk", chunk.id)
            merged_rows.append(annotated)
        if len(merged_rows) >= 500:
            break
    return (merged_rows, first_source) if merged_rows and first_source else None


def _markdown_tables(text: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.splitlines()]
    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("|") and line.endswith("|") and line.count("|") >= 2:
            current.append(line)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    output: list[dict[str, Any]] = []
    for group in groups:
        parsed = [[cell.strip() for cell in line.strip("|").split("|")] for line in group]
        parsed = [row for row in parsed if row and not all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in row)]
        if len(parsed) < 2:
            continue
        headers = [_safe_key(cell or f"col_{index + 1}") for index, cell in enumerate(parsed[0])]
        for row in parsed[1:]:
            row = row + [""] * (len(headers) - len(row))
            output.append({headers[index]: row[index] for index in range(len(headers))})
    return output


def _coerce_value(value: Any, field_type: ExtractionFieldType) -> Any:
    if value is None:
        return None
    if field_type == ExtractionFieldType.TEXT:
        return str(value).strip()
    if field_type == ExtractionFieldType.EMAIL:
        text = str(value).strip()
        match = TYPE_PATTERNS[ExtractionFieldType.EMAIL].search(text)
        return match.group(0) if match else text
    if field_type == ExtractionFieldType.PHONE:
        return str(value).strip()
    if field_type == ExtractionFieldType.NUMBER:
        return _to_number(value)
    if field_type == ExtractionFieldType.CURRENCY:
        return _to_number(value)
    if field_type == ExtractionFieldType.DATE:
        return _to_date(value)
    if field_type == ExtractionFieldType.BOOLEAN:
        return _to_bool(value)
    if field_type == ExtractionFieldType.LIST:
        if isinstance(value, list):
            return value
        return [item.strip() for item in re.split(r"[,;\n]", str(value)) if item.strip()]
    if field_type == ExtractionFieldType.TABLE:
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            rows = []
            for k, v in value.items():
                if isinstance(v, dict):
                    row = {"item": k}
                    row.update(v)
                    rows.append(row)
                else:
                    rows.append({"item": k, "amount": v})
            return rows
        return []
    if field_type == ExtractionFieldType.OBJECT:
        return value if isinstance(value, dict) else {}
    return value


def _to_number(value: Any) -> Optional[Union[int, float]]:
    text = str(value).strip()
    match = TYPE_PATTERNS[ExtractionFieldType.NUMBER].search(text.replace(",", ""))
    if not match:
        return None
    number = match.group(0).replace(",", "")
    try:
        parsed = float(number)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _to_date(value: Any) -> Optional[date]:
    text = str(value).strip().replace(",", "")
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%m-%d-%Y",
        "%d-%m-%Y",
        "%B %d %Y",
        "%b %d %Y",
        "%B %Y",
        "%b %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_bool(value: Any) -> Optional[bool]:
    text = str(value).strip().lower()
    if text in {"true", "yes", "pass", "approved", "compliant"}:
        return True
    if text in {"false", "no", "fail", "rejected", "non-compliant"}:
        return False
    return None


def _field_result(
    field: ExtractionSchemaField,
    value: Any,
    raw_value: Any,
    confidence: float,
    evidence: list[ExtractionEvidence],
) -> ExtractionFieldResult:
    return ExtractionFieldResult(
        key=field.key,
        label=field.label or field.key,
        type=field.type,
        required=field.required,
        value=value,
        raw_value=raw_value,
        confidence=round(confidence, 3),
        valid=True,
        evidence=evidence,
    )


def _build_pydantic_model(schema: ExtractionLabSchema):
    model_name = _model_name(schema.name)
    field_defs: dict[str, tuple[Any, Any]] = {}
    used_internal_names: set[str] = set()
    for field in schema.fields:
        internal = _unique_identifier(field.key, used_internal_names)
        py_type = _python_type(field)
        annotation = Optional[py_type]
        field_defs[internal] = (
            annotation,
            PydanticField(
                default=None,
                alias=field.key,
                description=field.description or field.label or field.key,
            ),
        )
    model = create_model(
        model_name,
        __config__=ConfigDict(extra="forbid", populate_by_name=True),
        **field_defs,
    )
    return model_name, model, _generated_code(schema, model_name)


def _python_type(field: ExtractionSchemaField) -> Any:
    if field.type == ExtractionFieldType.NUMBER:
        return Union[int, float]
    if field.type == ExtractionFieldType.CURRENCY:
        return float
    if field.type == ExtractionFieldType.DATE:
        return date
    if field.type == ExtractionFieldType.BOOLEAN:
        return bool
    if field.type == ExtractionFieldType.LIST:
        if field.children:
            return list[dict[str, Any]]
        return list[str]
    if field.type == ExtractionFieldType.TABLE:
        return list[dict[str, Any]]
    if field.type == ExtractionFieldType.OBJECT:
        return dict[str, Any]
    return str


def _generated_code(schema: ExtractionLabSchema, model_name: str) -> str:
    lines = [
        "from datetime import date",
        "from typing import Any",
        "",
        "from pydantic import BaseModel, Field",
        "",
        "",
        f"class {model_name}(BaseModel):",
    ]
    if not schema.fields:
        lines.append("    pass")
        return "\n".join(lines)
    used: set[str] = set()
    for field in schema.fields:
        internal = _unique_identifier(field.key, used)
        annotation = _annotation_for_code(_python_type(field))
        args = ['default=None']
        if internal != field.key:
            args.append(f'alias="{field.key}"')
        if field.description:
            args.append(f'description="{_escape(field.description)}"')
        lines.append(f"    {internal}: {annotation} | None = Field({', '.join(args)})")
    return "\n".join(lines)


def _annotation_for_code(py_type: Any) -> str:
    origin = get_origin(py_type)
    if origin is Union:
        return "int | float"
    if py_type is str:
        return "str"
    if py_type is float:
        return "float"
    if py_type is bool:
        return "bool"
    if py_type is date:
        return "date"
    if origin is list:
        args = get_args(py_type)
        if args and args[0] is str:
            return "list[str]"
        return "list[dict[str, Any]]"
    if origin is dict:
        return "dict[str, Any]"
    return "Any"


def _validate_required(
    schema: ExtractionLabSchema,
    data: dict[str, Any],
) -> list[ExtractionValidationError]:
    errors: list[ExtractionValidationError] = []
    for field in schema.fields:
        if field.required and _missing(data.get(field.key)):
            errors.append(
                ExtractionValidationError(
                    loc=field.key,
                    msg="Required field was not extracted",
                    type="missing_required_field",
                )
            )
    return errors


def _format_validation_errors(exc: ValidationError) -> list[ExtractionValidationError]:
    errors: list[ExtractionValidationError] = []
    for error in exc.errors():
        errors.append(
            ExtractionValidationError(
                loc=".".join(str(part) for part in error.get("loc", [])),
                msg=str(error.get("msg", "Validation error")),
                type=str(error.get("type", "value_error")),
            )
        )
    return errors


def _errors_by_key(errors: list[ExtractionValidationError]) -> dict[str, str]:
    out: dict[str, str] = {}
    for error in errors:
        key = error.loc.split(".", 1)[0]
        out.setdefault(key, error.msg)
    return out


def _run_warnings(
    parser_result: ParserRunResult,
    chunks: list[SourceChunk],
    errors: list[ExtractionValidationError],
) -> list[str]:
    warnings: list[str] = []
    if parser_result.status != ParserStatus.OK:
        warnings.append(parser_result.error or "Parser did not finish cleanly")
    if len(chunks) > 300:
        warnings.append("Only the first 300 chunks are returned in the response preview")
    if errors:
        warnings.append("Some fields are missing or failed Pydantic validation")
    return warnings


def _evidence(chunk: SourceChunk) -> ExtractionEvidence:
    return ExtractionEvidence(
        chunk_id=chunk.id,
        page=chunk.page,
        type=chunk.type,
        text_preview=preview_text(chunk.text, 260),
        bbox=chunk.bbox,
    )


def _field_tokens(field: ExtractionSchemaField) -> set[str]:
    return _tokens(" ".join([field.key, field.label, field.description or ""]))


def _field_label_norms(field: ExtractionSchemaField) -> list[str]:
    values = [field.key, field.label]
    if field.description:
        values.append(field.description)
    return [_normalize_text(value) for value in values if value]


def _field_wants_images(field: ExtractionSchemaField) -> bool:
    tokens = _field_tokens(field)
    return bool(tokens.intersection({"image", "images", "figure", "figures", "chart", "charts", "visual", "visuals"}))


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9]+", text.lower())
        if token not in STOPWORDS and len(token) > 1
    }


def _normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _trim_value(value: str) -> str:
    clean = value.strip().strip("|").strip()
    clean = re.sub(r"\s{2,}", " ", clean)
    return clean[:500]


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_key(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return clean or "value"


def _safe_identifier(value: str) -> str:
    clean = re.sub(r"\W+", "_", value.strip()).strip("_")
    if not clean:
        clean = "field"
    if clean[0].isdigit():
        clean = f"field_{clean}"
    if clean.startswith("_"):
        clean = f"field{clean}"
    return clean


def _unique_identifier(value: str, used: set[str]) -> str:
    base = _safe_identifier(value)
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def _model_name(value: str) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", value or "")
    name = "".join(word[:1].upper() + word[1:] for word in words) or "ExtractionResult"
    if name[0].isdigit():
        name = f"Extraction{name}"
    return name


def save_schema_template(name: str, schema: ExtractionLabSchema) -> ExtractionLabSchemaTemplate:
    """Save a schema as a JSON template file in data/extraction_schemas/."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name.strip()).strip("_").lower() or "custom_schema"
    schema_dir = Path(__file__).resolve().parents[3] / "data" / "extraction_schemas"
    schema_dir.mkdir(parents=True, exist_ok=True)

    path = schema_dir / f"{safe}.json"
    path.write_text(json.dumps(schema.model_dump(), indent=2), encoding="utf-8")

    return ExtractionLabSchemaTemplate(
        id=safe,
        label=_template_label(safe, schema.name),
        filename=path.name,
        schema_definition=schema,
    )


def delete_schema_template(schema_id: str) -> bool:
    """Delete a schema JSON template file from data/extraction_schemas/."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", schema_id.strip()).strip("_").lower()
    schema_dir = Path(__file__).resolve().parents[3] / "data" / "extraction_schemas"
    path = schema_dir / f"{safe}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def _template_label(stem: str, model_name: str) -> str:
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", model_name)
    if not words:
        words = re.findall(r"[a-zA-Z0-9]+", stem)
    return " ".join(word[:1].upper() + word[1:] for word in words) or stem


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


async def _save_extraction_result_to_db(session: AsyncSession, input_id: str, response: ExtractionRunResponse) -> None:
    try:
        from app.db.models import ExtractionResultModel
        db_result = ExtractionResultModel(
            run_id=response.run_id,
            input_id=input_id,
            schema_name=response.schema_definition.get("name") or "default",
            response_json=response.model_dump(mode="json"),
        )
        session.add(db_result)
        await session.commit()
    except Exception as e:
        print(f"Failed to persist extraction result in Postgres: {e}")
