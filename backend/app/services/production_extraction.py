"""DB-backed case, document, search, and extraction operations.

This module coordinates RAG extractions, case query searches, uploads attachment mapping,
and schema-constrained agentic extraction executions.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
import logging
from typing import Any, BinaryIO

from fastapi import HTTPException
from app.services.parsers import quick as document_parser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentModel, EvidenceItemModel, ExtractionJobModel, FieldCandidateModel, FieldResultModel
from app.db.compat import ensure_runtime_settings_columns
from app.db.repositories.case_repo import CaseRepository
from app.db.repositories.document_repo import DocumentRepository
from app.db.repositories.evidence_repo import EvidenceRepository
from app.db.repositories.job_repo import ExtractionJobRepository
from app.extraction.agentic_controller import AgenticFieldExtractor, ConsistencyReport, critic_issues, detect_conflict
from app.extraction.candidate_resolver import resolve_candidates
from app.extraction.document_map import build_document_map_from_evidence
from app.extraction.field_extractor import FieldExtractor, _clean_value, sanitize_extracted_value
from app.extraction.planner import FieldRetrievalPlanner
from app.extraction.progressive_retrieval import ProgressiveRetriever
from app.extraction.schema_constrained_extractor import (
    SCHEMA_EXTRACTION_MODEL,
    SchemaConstrainedExtractor,
    clean_schema_value,
    validate_schema_value_quality,
    write_extraction_audit,
)
from app.extraction.validator import validate_field
from app.models.document import DocumentMetadata, DocumentSource, DocumentStatus, DocumentType, utcnow
from app.models.extraction import (
    CaseCreate,
    EvidenceSource,
    ExtractionRequest,
    ExtractionResult,
    FieldCandidate,
    FieldResult,
    SearchHit,
    SearchRequest,
)
from app.models.settings import RuntimeSettings
from app.services.artifact_store import ArtifactStore
from app.services.embedding import embed_text

logger = logging.getLogger(__name__)


async def create_case_db(session: AsyncSession, payload: CaseCreate) -> ExtractionCase:
    """Create a new extraction case record in the database."""
    repo = CaseRepository(session)
    case = await repo.create(payload.title, payload.user_id, getattr(payload, "metadata_json", None) or {})
    await session.commit()
    return _case_model(case)


async def list_cases_db(session: AsyncSession) -> list[ExtractionCase]:
    """Retrieve all recent cases from the database."""
    repo = CaseRepository(session)
    return [_case_model(case) for case in await repo.list_recent()]


async def get_case_db(session: AsyncSession, case_id: str) -> ExtractionCase:
    """Retrieve a single case record by its unique ID."""
    case = await CaseRepository(session).get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return _case_model(case)


async def get_case_progress_db(session: AsyncSession, case_id: str) -> dict[str, Any]:
    """Retrieve parsing progression statistics (total documents, pages, parsed items) for a case."""
    case = await CaseRepository(session).get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return await CaseRepository(session).get_progress(case_id)


async def list_case_documents_db(session: AsyncSession, case_id: str) -> list[DocumentMetadata]:
    """Fetch metadata for all documents registered within a specific case folder."""
    docs = await DocumentRepository(session).list_by_case(case_id)
    return [_document_model(doc) for doc in docs]


async def attach_upload_to_case_db(
    session: AsyncSession,
    case_id: str,
    file_obj: BinaryIO,
    filename: str,
    mime_type: str,
    user_metadata: dict[str, Any] | None = None,
) -> DocumentMetadata:
    """Register an uploaded document file, store it on disk, and queue it for quick parsing.

    Saves the file to physical storage, calculates its SHA256 checksum, handles duplicate mapping
    checks, inserts a Document record into the database, and pushes a 'quick_parse' job into
    the queue table.
    """
    case = await CaseRepository(session).get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    document_id = f"doc-{uuid.uuid4().hex[:12]}"
    store = ArtifactStore()
    storage_path, file_hash, size = store.store_raw(case_id, document_id, file_obj, filename)
    store.remember_hash(case_id, file_hash, storage_path)

    repo = DocumentRepository(session)
    existing = await repo.get_by_hash(file_hash)
    inferred = {"reused_from_document_id": existing.document_id} if existing and existing.document_id != document_id else {}
    doc = await repo.create(
        document_id=document_id,
        case_id=case_id,
        filename=Path(filename).name,
        mime_type=mime_type or "application/octet-stream",
        file_hash=file_hash,
        storage_path=storage_path,
        size_bytes=size,
        user_metadata=user_metadata or {},
        inferred_metadata=inferred,
    )

    # Synchronously resolve initial page count and document type via quick parser
    page_count = 1
    doc_type = "other"
    if storage_path:
        try:
            parsed = document_parser.parse_document(Path(storage_path))
            page_count = int(parsed.get("page_count") or 1)
            doc_type = parsed.get("document_type") or "other"
        except Exception:
            pass

    inferred["document_type"] = doc_type
    priority_map = {
        "financial_statement": 100,
        "annual_report": 90,
        "bank_statement": 80,
        "proxy_form": 50,
    }
    priority = priority_map.get(doc_type, 10)

    # Update state to processing and enqueued
    await repo.update_parser_status(
        document_id,
        status="processing",
        page_count=page_count,
        priority=priority,
        inferred_metadata=inferred,
    )
    await repo.enqueue_job(document_id, "parse_and_index", priority=priority)

    case.status = "parsing"
    await session.commit()
    return _document_model(doc)


async def search_case_db(session: AsyncSession, case_id: str, payload: SearchRequest) -> list[SearchHit]:
    """Perform a hybrid dense/sparse RAG query search across all documents within a case.

    Generates the dense query vector embedding via the embedding API, then calls `hybrid_search`
    on the Evidence repository.
    """
    repo = EvidenceRepository(session)
    query_embedding = await asyncio.to_thread(embed_text, payload.query)
    rows = await repo.hybrid_search(
        case_id=case_id,
        query=payload.query,
        query_embedding=query_embedding,
        top_k=payload.top_k,
    )
    return [
        SearchHit(
            score=float(row.get("hybrid_score") or row.get("score") or 0),
            evidence=_evidence_source(row),
        )
        for row in rows
    ]


async def list_document_evidence_db(session: AsyncSession, document_id: str) -> list[dict[str, Any]]:
    """Retrieve all parsed layout and text chunks (evidence items) for a single document."""
    rows = await EvidenceRepository(session).list_by_document(document_id)
    return [
        {
            "evidence_id": item.evidence_id,
            "document_id": item.document_id,
            "page_number": item.page_number,
            "source_type": item.source_type,
            "text": item.text,
            "markdown": item.markdown,
            "bbox": item.bbox,
            "confidence": item.confidence,
            "metadata": item.metadata_json,
        }
        for item in rows
    ]


async def run_case_extraction_db(
    session: AsyncSession,
    case_id: str,
    payload: ExtractionRequest,
    *,
    agentic: bool = False,
) -> ExtractionResult:
    """Coordinate schema-based data extraction from all indexed documents in a case.

    Workflow:
    1. Creates a new ExtractionJob record.
    2. Runs either schema-constrained agentic extraction or standard field-by-field extraction loops.
    3. For standard extraction:
       * Plans retrieval criteria per field.
       * Runs progressive retrieval attempts (FTS + vector search) up to 3 times if values are missing
         or fail schema validations.
       * Resolves candidate values, runs regex/type validation constraints, and logs model cost audits.
    4. Updates final job status ('needs_review' or 'completed') based on validation results and commits.
    """
    schema = payload.output_schema if payload.output_schema else None
    if schema is None:
        schema = {"type": "object", "properties": {}}

    await ensure_runtime_settings_columns(session)
    await session.commit()

    case = await CaseRepository(session).get(case_id)
    case_settings_dict = getattr(case, "settings", None) or {}
    request_settings = payload.settings or {}
    resolved_settings_dict = {**case_settings_dict, **request_settings}
    try:
        settings_obj = RuntimeSettings.model_validate(resolved_settings_dict)
    except Exception:
        settings_obj = RuntimeSettings()

    job_repo = ExtractionJobRepository(session)
    job = await job_repo.create_job(case_id=case_id, schema_id=payload.schema_id, schema_json=schema)
    job_id = job.job_id
    job.settings = settings_obj.model_dump()
    job.status = "running"
    await session.commit()

    try:
        planner = FieldRetrievalPlanner()
        retriever = ProgressiveRetriever(EvidenceRepository(session))
        if agentic:
            return await _run_schema_constrained_case_extraction_db(
                session=session,
                case_id=case_id,
                payload=payload,
                schema=schema,
                job=job,
                job_repo=job_repo,
                planner=planner,
                retriever=retriever,
                settings=settings_obj,
            )

        extractor = _extractor_for_mode(agentic)
        consistency = ConsistencyReport(adk_available=getattr(extractor, "adk_available", False)) if agentic else ConsistencyReport()

        fields: dict[str, FieldResult] = {}
        properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
        required = set(schema.get("required", []) if isinstance(schema.get("required"), list) else [])
        logger.info("production_extraction: start case=%s fields=%d agentic=%s", case_id, len(properties), agentic)
        for field_path, field_schema in properties.items():
            logger.debug("production_extraction: extract_field field=%s schema_type=%s", field_path, str(field_schema.get("type") or "string"))
            plan = planner.plan(field_path, field_schema, settings=settings_obj)
            field_row = await job_repo.add_field_result(job.job_id, field_path)
            candidates = []
            final_status = "missing"
            final_value = None
            final_confidence = 0.0
            validation_errors: list[str] = []
            was_missing = False
            max_retries = settings_obj.queries.empty_results_max_retry
            for attempt_number in range(1, max_retries + 1):
                pack = await retriever.retrieve(case_id, plan, attempt=attempt_number, settings=settings_obj)
                logger.debug("production_extraction: retrieve field=%s attempt=%d pack_size=%d", field_path, attempt_number, pack.estimated_text_tokens if hasattr(pack, 'estimated_text_tokens') else 0)
                await job_repo.add_attempt(
                    field_row.field_result_id,
                    attempt_number,
                    evidence_pack=pack.model_dump(),
                    input_tokens=pack.estimated_text_tokens,
                    model_used=consistency.model_used if agentic else None,
                )
                candidates = await asyncio.to_thread(extractor.extract, field_path, field_schema, pack)
                if not candidates:
                    logger.debug("production_extraction: no_candidates field=%s attempt=%d", field_path, attempt_number)
                    was_missing = True
                    consistency.null_fields_detected += 1 if attempt_number == 1 else 0
                    consistency.null_retries += 1 if agentic and attempt_number < max_retries else 0
                    continue
                if was_missing:
                    consistency.recovered_nulls += 1
                if detect_conflict([candidate.value for candidate in candidates]):
                    consistency.candidate_conflicts += 1
                if candidates:
                    logger.info("production_extraction: candidates field=%s attempt=%d count=%d", field_path, attempt_number, len(candidates))
                    final_value, final_status, final_confidence = resolve_candidates(candidates)
                    validation_errors = validate_field(final_value, field_schema, field_path in required)
                    if validation_errors and final_status == "validated":
                        final_status = "invalid"
                    logger.info("production_extraction: field_result field=%s status=%s confidence=%s", field_path, final_status, final_confidence)
                    if final_status == "validated":
                        break
            for candidate in candidates:
                candidate.value = sanitize_extracted_value(candidate.value, field_path, str(field_schema.get("type") or "string"))
                await job_repo.add_candidate(
                    field_row.field_result_id,
                    value=candidate.value,
                    confidence=candidate.confidence,
                    evidence_ids=candidate.evidence_ids,
                    extraction_method=candidate.extraction_method,
                )
            field_row.value = sanitize_extracted_value(final_value, field_path, str(field_schema.get("type") or "string"))
            field_row.status = final_status
            field_row.confidence = final_confidence
            field_row.validation_errors = validation_errors
            field_row.attempt_count = max_retries
            cleaned_final = sanitize_extracted_value(final_value, field_path, str(field_schema.get("type") or "string"))
            fields[field_path] = FieldResult(
                field_path=field_path,
                value=cleaned_final,
                status=final_status,
                confidence=final_confidence,
                candidates=[
                    FieldCandidate(
                        candidate_id=f"cand-{uuid.uuid4().hex[:8]}",
                        field_path=field_path,
                        value=candidate.value,
                        normalized_value=candidate.value,
                        confidence=candidate.confidence,
                        evidence=[
                            EvidenceSource(
                                evidence_id=evidence_id,
                                document_id="",
                                filename="",
                                page_number=0,
                                source_type="text_block",
                            )
                            for evidence_id in candidate.evidence_ids
                        ],
                        extraction_method=candidate.extraction_method,
                    )
                    for candidate in candidates
                ],
                validation_errors=validation_errors,
            )

        final_json = {path: result.value for path, result in fields.items() if result.status in {"validated", "low_confidence"}}
        consistency.critic_issues = critic_issues(final_json, required) if agentic else []
        status = "needs_review" if any(result.status != "validated" for result in fields.values()) or consistency.critic_issues else "completed"
        await job_repo.update_status(job.job_id, status)
        await session.commit()
        logger.info("production_extraction: complete case=%s status=%s fields=%d", case_id, status, len(fields))
        return ExtractionResult(
            job_id=job.job_id,
            case_id=case_id,
            schema_id=payload.schema_id,
            status=status,
            fields=fields,
            final_json=final_json,
            validation_report={
                "review_required_fields": [path for path, result in fields.items() if result.status != "validated"],
                "consistency": consistency.model_dump(),
                "retrieval_stats": {
                    "retrieval_mode": retriever.retrieval_stats.mode,
                    "dense_hits": retriever.retrieval_stats.dense_hits,
                    "sparse_hits": retriever.retrieval_stats.sparse_hits,
                },
            },
            started_at=job.started_at or utcnow(),
            completed_at=job.completed_at,
        )
    except Exception:
        logger.exception("production_extraction: failed case=%s job=%s", case_id, job_id)
        await session.rollback()
        await job_repo.update_status(job_id, "failed")
        await session.commit()
        raise


async def _run_schema_constrained_case_extraction_db(
    *,
    session: AsyncSession,
    case_id: str,
    payload: ExtractionRequest,
    schema: dict[str, Any],
    job: ExtractionJobModel,
    job_repo: ExtractionJobRepository,
    planner: FieldRetrievalPlanner,
    retriever: ProgressiveRetriever,
    settings: RuntimeSettings | None = None,
) -> ExtractionResult:
    """Execute schema-constrained extraction using LLM structured generation modes.

    Performs dense extraction of all fields in the schema concurrently, feeding the LLM
    with mapped table structures and critical front-matter page evidence. Runs targeted
    retry extraction routines for missing, low-confidence, or validation-failing outputs.
    """
    if settings is None:
        settings = RuntimeSettings()
    properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required", []) if isinstance(schema.get("required"), list) else [])
    logger.info("production_extraction: schema_mode start case=%s fields=%d", case_id, len(properties))
    deterministic_extractor = AgenticFieldExtractor()
    consistency = ConsistencyReport(model_used=SCHEMA_EXTRACTION_MODEL)
    field_rows: dict[str, FieldResultModel] = {}
    field_packs = {}

    for field_path, field_schema in properties.items():
        plan = planner.plan(field_path, field_schema, settings=settings)
        field_row = await job_repo.add_field_result(job.job_id, field_path)
        pack = await retriever.retrieve(case_id, plan, attempt=3, settings=settings)
        await job_repo.add_attempt(
            field_row.field_result_id,
            1,
            evidence_pack=pack.model_dump(),
            input_tokens=pack.estimated_text_tokens,
            model_used=SCHEMA_EXTRACTION_MODEL,
        )
        field_rows[field_path] = field_row
        field_packs[field_path] = pack

    cover_evidence = await _case_cover_evidence(session, case_id)

    all_evidence: list[dict[str, Any]] = list(cover_evidence)
    for pack in field_packs.values():
        for row in [*pack.tables, *pack.text_snippets]:
            all_evidence.append(row)

    document_map = build_document_map_from_evidence(all_evidence)
    logger.info(
        "production_extraction: document_map built pages=%d headings=%d tables=%d",
        document_map.page_count,
        _count_map_headings(document_map.heading_tree),
        len(document_map.tables),
    )

    schema_result = await asyncio.to_thread(
        SchemaConstrainedExtractor().extract,
        schema,
        field_packs,
        cover_evidence,
        document_map,
    )
    logger.info("production_extraction: schema_result used_llm=%s error=%s fields_extracted=%d", schema_result.used_llm, schema_result.error, len(schema_result.data))
    fields: dict[str, FieldResult] = {}

    if schema_result.error:
        consistency.critic_issues.append(f"Schema-constrained extractor failed: {schema_result.error}")

    for field_path, field_schema in properties.items():
        field_row = field_rows[field_path]
        expected = str(field_schema.get("type") or "string") if isinstance(field_schema, dict) else "string"
        raw_value = schema_result.data.get(field_path)
        cleaned_value = clean_schema_value(raw_value, expected)
        confidence = schema_result.confidence_by_field.get(field_path, 0.0)
        logger.debug("production_extraction: schema_field field=%s raw_value_present=%s confidence=%s", field_path, raw_value is not None, confidence)
        evidence_ids = schema_result.evidence_ids_by_field.get(field_path, field_packs[field_path].evidence_ids[:4])
        extraction_method = "llm_text" if schema_result.used_llm else "keyword_rule"
        validation_errors = []
        if isinstance(field_schema, dict):
            validation_errors = validate_field(cleaned_value, field_schema, field_path in required)
            validation_errors.extend(validate_schema_value_quality(field_path, field_schema, cleaned_value))

        retry_count = 0
        retry_candidates = []
        retry_cleaned = None
        retry_confidence = 0.0
        if (cleaned_value is None and field_path in required) or validation_errors or (confidence < 0.7 and cleaned_value is not None):
            logger.info("production_extraction: retry_triggered field=%s reason=%s", field_path, "None" if cleaned_value is None else "validation" if validation_errors else "low_confidence")
            retry_count = 1
            consistency.null_retries += 1
            await job_repo.add_attempt(
                field_row.field_result_id,
                2,
                evidence_pack=field_packs[field_path].model_dump(),
                input_tokens=field_packs[field_path].estimated_text_tokens,
                model_used="deterministic_retry",
                error="retrying missing, invalid, or low-confidence schema result",
            )
            retry_candidates = await asyncio.to_thread(
                deterministic_extractor.extract,
                field_path,
                field_schema,
                field_packs[field_path],
            )
            if retry_candidates:
                retry_value, retry_status, retry_confidence = resolve_candidates(retry_candidates)
                retry_cleaned = clean_schema_value(retry_value, expected)
                retry_errors = validate_field(retry_cleaned, field_schema, field_path in required) if isinstance(field_schema, dict) else []
                retry_errors.extend(validate_schema_value_quality(field_path, field_schema, retry_cleaned) if isinstance(field_schema, dict) else [])
                if retry_cleaned is not None and (not retry_errors or cleaned_value is None):
                    cleaned_value = retry_cleaned
                    confidence = retry_confidence
                    evidence_ids = retry_candidates[0].evidence_ids
                    extraction_method = retry_candidates[0].extraction_method
                    validation_errors = retry_errors
                    if retry_status == "validated" and not retry_errors:
                        consistency.recovered_nulls += 1
            logger.info("production_extraction: retry_result field=%s recovered=%s confidence=%s", field_path, retry_cleaned is not None, retry_confidence if retry_candidates else 0)

        if cleaned_value is None:
            final_status = "missing"
            if field_path in required:
                consistency.null_fields_detected += 1
        elif validation_errors:
            final_status = "invalid"
        elif confidence >= 0.7:
            final_status = "validated"
        else:
            final_status = "low_confidence"

        candidates = []
        if cleaned_value is not None:
            await job_repo.add_candidate(
                field_row.field_result_id,
                value=cleaned_value,
                confidence=confidence,
                evidence_ids=evidence_ids,
                extraction_method=extraction_method,
            )
            candidates.append(
                FieldCandidate(
                    candidate_id=f"cand-{uuid.uuid4().hex[:8]}",
                    field_path=field_path,
                    value=cleaned_value,
                    normalized_value=cleaned_value,
                    confidence=confidence,
                    evidence=[
                        EvidenceSource(
                            evidence_id=evidence_id,
                            document_id="",
                            filename="",
                            page_number=0,
                            source_type="text_block",
                        )
                        for evidence_id in evidence_ids
                    ],
                    extraction_method=extraction_method,
                )
            )

        for candidate in retry_candidates:
            candidate.value = clean_schema_value(candidate.value, expected)
            if candidate.value is None:
                continue
            await job_repo.add_candidate(
                field_row.field_result_id,
                value=candidate.value,
                confidence=candidate.confidence,
                evidence_ids=candidate.evidence_ids,
                extraction_method=candidate.extraction_method,
            )

        field_row.value = cleaned_value
        field_row.status = final_status
        field_row.confidence = confidence
        field_row.validation_errors = validation_errors
        field_row.attempt_count = 1 + retry_count
        fields[field_path] = FieldResult(
            field_path=field_path,
            value=cleaned_value,
            status=final_status,
            confidence=confidence,
            candidates=candidates,
            validation_errors=validation_errors,
        )

        audit_item = schema_result.audit.get(field_path)
        if audit_item:
            audit_item.raw_value = raw_value
            audit_item.cleaned_value = cleaned_value
            audit_item.validation_errors = validation_errors
            audit_item.confidence = confidence
            audit_item.retry_count = retry_count
            audit_item.extraction_method = extraction_method
            audit_item.evidence_ids = evidence_ids

    final_json = {path: result.value for path, result in fields.items() if result.status in {"validated", "low_confidence"}}
    consistency.critic_issues.extend(critic_issues(final_json, required))
    status = "needs_review" if any(result.status != "validated" for result in fields.values()) or consistency.critic_issues else "completed"
    audit_path = write_extraction_audit(
        job.job_id,
        case_id,
        payload.schema_id,
        "schema_constrained",
        schema_result.audit,
        model_used=schema_result.model_used,
        error=schema_result.error,
    )
    await job_repo.update_status(job.job_id, status)
    await session.commit()
    logger.info("production_extraction: schema_mode complete case=%s status=%s fields=%d critic_issues=%d", case_id, status, len(fields), len(consistency.critic_issues))
    return ExtractionResult(
        job_id=job.job_id,
        case_id=case_id,
        schema_id=payload.schema_id,
        status=status,
        fields=fields,
        final_json=final_json,
        validation_report={
            "review_required_fields": [path for path, result in fields.items() if result.status != "validated"],
            "consistency": consistency.model_dump(),
            "retrieval_stats": {
                "retrieval_mode": retriever.retrieval_stats.mode,
                "dense_hits": retriever.retrieval_stats.dense_hits,
                "sparse_hits": retriever.retrieval_stats.sparse_hits,
            },
            "schema_extraction": {
                "used_llm": schema_result.used_llm,
                "error": schema_result.error,
                "audit_path": audit_path,
                "model_used": schema_result.model_used,
            },
        },
        started_at=job.started_at or utcnow(),
        completed_at=job.completed_at,
    )


async def get_job_db(session: AsyncSession, job_id: str) -> ExtractionResult:
    """Retrieve an ExtractionJob run result logs and populated field results."""
    job = await ExtractionJobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Extraction job not found")
    stmt = select(FieldResultModel).where(FieldResultModel.job_id == job_id)
    result = await session.execute(stmt)
    field_rows = result.scalars().all()
    fields: dict[str, FieldResult] = {}
    final_json: dict[str, Any] = {}
    for row in field_rows:
        candidates = await _field_candidates(session, row)
        field = FieldResult(
            field_path=row.field_path,
            value=row.value,
            status=row.status,
            confidence=row.confidence,
            candidates=candidates,
            validation_errors=row.validation_errors or [],
        )
        fields[row.field_path] = field
        if row.status in {"validated", "low_confidence", "human_corrected"}:
            final_json[row.field_path] = row.value
    return ExtractionResult(
        job_id=job.job_id,
        case_id=job.case_id,
        schema_id=job.schema_id,
        status=job.status,
        fields=fields,
        final_json=final_json,
        validation_report={"review_required_fields": [path for path, field in fields.items() if field.status != "validated"]},
        started_at=job.started_at or utcnow(),
        completed_at=job.completed_at,
    )


async def list_job_candidates_db(session: AsyncSession, job_id: str) -> list[dict[str, Any]]:
    """Retrieve all resolved value candidates generated during extraction runs for audit comparisons."""
    stmt = (
        select(FieldCandidateModel, FieldResultModel.field_path)
        .join(FieldResultModel, FieldResultModel.field_result_id == FieldCandidateModel.field_result_id)
        .where(FieldResultModel.job_id == job_id)
    )
    result = await session.execute(stmt)
    return [
        {
            "candidate_id": candidate.candidate_id,
            "field_path": field_path,
            "value": candidate.value,
            "confidence": candidate.confidence,
            "evidence_ids": candidate.evidence_ids,
            "extraction_method": candidate.extraction_method,
        }
        for candidate, field_path in result.all()
    ]


async def retry_field_db(session: AsyncSession, job_id: str, field_path: str) -> ExtractionResult:
    """Manually re-run the extraction loop for a single schema field.

    Builds a temporary single-property schema request and runs the extraction engine.
    """
    job = await ExtractionJobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Extraction job not found")
    payload = ExtractionRequest(
        schema_id=job.schema_id,
        output_schema=job.schema_json or {"type": "object", "properties": {field_path: {"type": "string"}}},
        settings=job.settings,
    )
    return await run_case_extraction_db(session, job.case_id, payload)


async def _case_cover_evidence(session: AsyncSession, case_id: str) -> list[dict[str, Any]]:
    """Fetch the front-matter introduction page chunks of a document.

    Helps supply high-value metadata context (document type, company title, date of filing)
    to schema-constrained extractors.
    """
    rows = await EvidenceRepository(session).list_by_case(case_id)
    ranked = sorted(
        rows,
        key=lambda row: (
            0 if (row.page_number or 0) <= 2 else 1,
            row.page_number or 9999,
            0 if str(row.source_type).startswith("text") else 1,
            row.evidence_id,
        ),
    )
    evidence: list[dict[str, Any]] = []
    for row in ranked:
        if len(evidence) >= 10:
            break
        if (row.page_number or 0) > 3 and evidence:
            break
        text = row.markdown or row.text or ""
        if not str(text).strip():
            continue
        evidence.append(_evidence_row_from_model(row))
    return evidence


def _evidence_row_from_model(row: EvidenceItemModel) -> dict[str, Any]:
    """Map a database Evidence item into a key-value layout chunk dictionary."""
    return {
        "evidence_id": row.evidence_id,
        "document_id": row.document_id,
        "page_number": row.page_number,
        "source_type": row.source_type,
        "text": row.text,
        "markdown": row.markdown,
        "bbox": row.bbox,
        "confidence": row.confidence,
        "metadata_json": row.metadata_json,
    }


def _extractor_for_mode(agentic: bool) -> AgenticFieldExtractor | FieldExtractor:
    """Select the extraction strategy module class based on agentic requirements."""
    return AgenticFieldExtractor() if agentic else FieldExtractor()


async def _field_candidates(session: AsyncSession, row: FieldResultModel) -> list[FieldCandidate]:
    """Retrieve all child FieldCandidate rows for a given FieldResult, mapping them to Pydantic formats."""
    stmt = select(FieldCandidateModel).where(FieldCandidateModel.field_result_id == row.field_result_id)
    result = await session.execute(stmt)
    return [
        FieldCandidate(
            candidate_id=candidate.candidate_id,
            field_path=row.field_path,
            value=candidate.value,
            normalized_value=candidate.value,
            confidence=candidate.confidence,
            evidence=[
                EvidenceSource(
                    evidence_id=evidence_id,
                    document_id="",
                    filename="",
                    page_number=0,
                    source_type="text_block",
                )
                for evidence_id in candidate.evidence_ids
            ],
            extraction_method=candidate.extraction_method,
        )
        for candidate in result.scalars().all()
    ]


def _case_model(case: Any) -> ExtractionCase:
    """Map a DB Case model instance to the Pydantic API response model."""
    return ExtractionCase(
        case_id=case.case_id,
        user_id=case.user_id,
        title=case.title,
        status=case.status,
        document_ids=[],
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def _document_model(doc: DocumentModel) -> DocumentMetadata:
    """Map a DB Document model instance to the Pydantic API document metadata schema.

    Translates parser queue statuses to overall processing states (e.g. uploaded -> processing -> indexed).
    """
    status = DocumentStatus.FAILED if doc.parser_status == "failed" else DocumentStatus.OCR_DONE if doc.parser_status == "indexed" else DocumentStatus.PROCESSING if doc.parser_status in {"quick_parsed", "parsed"} else DocumentStatus.UPLOADED
    doc_type = DocumentType.OTHER
    inferred_type = (doc.inferred_metadata or {}).get("document_type")
    if inferred_type in DocumentType._value2member_map_:
        doc_type = DocumentType(inferred_type)
    elif inferred_type in {"annual_report", "financial_statement"}:
        doc_type = DocumentType.REPORT
    return DocumentMetadata(
        id=doc.document_id,
        name=doc.filename,
        type=doc_type,
        source=DocumentSource.UPLOAD,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        page_count=doc.page_count,
        status=status,
        tags=["case-upload", doc.case_id],
        uploaded_at=doc.created_at,
        processed_at=doc.updated_at,
        confidence=doc.confidence,
        notes=doc.failure_info.get("error") if isinstance(doc.failure_info, dict) else None,
    )


def _evidence_source(row: dict[str, Any]) -> EvidenceSource:
    """Map a search hit database dictionary to a RAG query evidence source hit model."""
    return EvidenceSource(
        evidence_id=str(row.get("evidence_id")),
        document_id=str(row.get("document_id") or ""),
        filename="",
        page_number=int(row.get("page_number") or 0),
        source_type=row.get("source_type") or "text_block",
        text=row.get("text") or row.get("markdown"),
        bbox=row.get("bbox"),
        confidence=row.get("confidence"),
    )


def _count_map_headings(nodes: list[Any]) -> int:
    """Count the total recursive headings parsed in a heading tree structure."""
    if not nodes:
        return 0
    return sum(1 + _count_map_headings(n.children) for n in nodes if hasattr(n, "children"))
