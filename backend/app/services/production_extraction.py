"""DB-backed case, document, search, and extraction operations."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentModel, EvidenceItemModel, ExtractionJobModel, FieldCandidateModel, FieldResultModel
from app.db.repositories.case_repo import CaseRepository
from app.db.repositories.document_repo import DocumentRepository
from app.db.repositories.evidence_repo import EvidenceRepository
from app.db.repositories.job_repo import ExtractionJobRepository
from app.extraction.agentic_controller import AgenticFieldExtractor, ConsistencyReport, critic_issues, detect_conflict
from app.extraction.candidate_resolver import resolve_candidates
from app.extraction.field_extractor import FieldExtractor
from app.extraction.planner import FieldRetrievalPlanner
from app.extraction.progressive_retrieval import ProgressiveRetriever
from app.extraction.validator import validate_field
from app.models.document import DocumentMetadata, DocumentSource, DocumentStatus, DocumentType, utcnow
from app.models.extraction import (
    CaseCreate,
    EvidenceSource,
    ExtractionCase,
    ExtractionRequest,
    ExtractionResult,
    FieldCandidate,
    FieldResult,
    SearchHit,
    SearchRequest,
)
from app.services.artifact_store import ArtifactStore


async def create_case_db(session: AsyncSession, payload: CaseCreate) -> ExtractionCase:
    repo = CaseRepository(session)
    case = await repo.create(payload.title, payload.user_id, getattr(payload, "metadata_json", None) or {})
    await session.commit()
    return _case_model(case)


async def list_cases_db(session: AsyncSession) -> list[ExtractionCase]:
    repo = CaseRepository(session)
    return [_case_model(case) for case in await repo.list_recent()]


async def get_case_db(session: AsyncSession, case_id: str) -> ExtractionCase:
    case = await CaseRepository(session).get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return _case_model(case)


async def get_case_progress_db(session: AsyncSession, case_id: str) -> dict[str, Any]:
    case = await CaseRepository(session).get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return await CaseRepository(session).get_progress(case_id)


async def list_case_documents_db(session: AsyncSession, case_id: str) -> list[DocumentMetadata]:
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
    await repo.enqueue_job(document_id, "quick_parse")
    case.status = "parsing"
    await session.commit()
    return _document_model(doc)


async def search_case_db(session: AsyncSession, case_id: str, payload: SearchRequest) -> list[SearchHit]:
    repo = EvidenceRepository(session)
    rows = await repo.hybrid_search(case_id=case_id, query=payload.query, top_k=payload.top_k)
    return [
        SearchHit(
            score=float(row.get("hybrid_score") or row.get("score") or 0),
            evidence=_evidence_source(row),
        )
        for row in rows
    ]


async def list_document_evidence_db(session: AsyncSession, document_id: str) -> list[dict[str, Any]]:
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
    schema = payload.output_schema if payload.output_schema else None
    if schema is None:
        # Keep compatibility with schema_id-only requests by using an empty object schema.
        schema = {"type": "object", "properties": {}}

    job_repo = ExtractionJobRepository(session)
    job = await job_repo.create_job(case_id=case_id, schema_id=payload.schema_id, schema_json=schema)
    planner = FieldRetrievalPlanner()
    retriever = ProgressiveRetriever(EvidenceRepository(session), use_api_embeddings=True)
    # Extraction quality for rich/nested schemas depends on the LLM extractor.
    # Repeatability is handled at the Extraction Lab layer by replaying the
    # cached result for identical document/schema/settings fingerprints.
    extractor = _extractor_for_mode(agentic)
    consistency = ConsistencyReport(adk_available=getattr(extractor, "adk_available", False)) if agentic else ConsistencyReport()

    fields: dict[str, FieldResult] = {}
    properties = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required", []) if isinstance(schema.get("required"), list) else [])
    for field_path, field_schema in properties.items():
        plan = planner.plan(field_path, field_schema)
        field_row = await job_repo.add_field_result(job.job_id, field_path)
        candidates = []
        final_status = "missing"
        final_value = None
        final_confidence = 0.0
        validation_errors: list[str] = []
        was_missing = False
        for attempt_number in [1, 2, 3]:
            pack = await retriever.retrieve(case_id, plan, attempt=attempt_number)
            await job_repo.add_attempt(
                field_row.field_result_id,
                attempt_number,
                evidence_pack=pack.model_dump(),
                input_tokens=pack.estimated_text_tokens,
                model_used=consistency.model_used if agentic else None,
            )
            candidates = extractor.extract(field_path, field_schema, pack)
            if not candidates:
                was_missing = True
                consistency.null_fields_detected += 1 if attempt_number == 1 else 0
                consistency.null_retries += 1 if agentic and attempt_number < 3 else 0
                continue
            if was_missing:
                consistency.recovered_nulls += 1
            if detect_conflict([candidate.value for candidate in candidates]):
                consistency.candidate_conflicts += 1
            if candidates:
                final_value, final_status, final_confidence = resolve_candidates(candidates)
                validation_errors = validate_field(final_value, field_schema, field_path in required)
                if validation_errors and final_status == "validated":
                    final_status = "invalid"
                if final_status == "validated":
                    break
        for candidate in candidates:
            await job_repo.add_candidate(
                field_row.field_result_id,
                value=candidate.value,
                confidence=candidate.confidence,
                evidence_ids=candidate.evidence_ids,
                extraction_method=candidate.extraction_method,
            )
        field_row.value = final_value
        field_row.status = final_status
        field_row.confidence = final_confidence
        field_row.validation_errors = validation_errors
        field_row.attempt_count = 3
        fields[field_path] = FieldResult(
            field_path=field_path,
            value=final_value,
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
        },
        started_at=job.started_at or utcnow(),
        completed_at=job.completed_at,
    )


async def get_job_db(session: AsyncSession, job_id: str) -> ExtractionResult:
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
    job = await ExtractionJobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Extraction job not found")
    payload = ExtractionRequest(schema_id=job.schema_id, output_schema=job.schema_json or {"type": "object", "properties": {field_path: {"type": "string"}}})
    return await run_case_extraction_db(session, job.case_id, payload)


def _extractor_for_mode(agentic: bool) -> AgenticFieldExtractor | FieldExtractor:
    return AgenticFieldExtractor()


async def _field_candidates(session: AsyncSession, row: FieldResultModel) -> list[FieldCandidate]:
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
