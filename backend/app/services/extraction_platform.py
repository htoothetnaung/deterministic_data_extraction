"""Production-style case, schema, extraction, review, and export services.

This module intentionally uses local in-memory persistence for the MVP. The
interfaces mirror the storage model in agent.md so the dictionaries can be
replaced by database repositories without changing endpoint behavior.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import settings
from app.data.mock import store
from app.models.document import DocumentMetadata, DocumentSource, DocumentStatus, DocumentType, utcnow
from app.models.extraction import (
    CaseCreate,
    EvidenceSource,
    ExportBundle,
    ExtractionCase,
    ExtractionRequest,
    ExtractionResult,
    FieldCandidate,
    FieldResult,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    SearchHit,
    SearchRequest,
    TextBlock,
)
from app.models.extraction_lab import ExtractionFieldType, ExtractionLabSchema, ExtractionSchemaField
from app.models.parser_benchmark import ParserRunResult, ParserStatus
from app.models.review import ReviewAction, ReviewPayload
from app.models.schema import ExtractionSchema, FieldExtractionHints, SchemaCreate, SchemaUpdate, SchemaValidationResult
from app.services.extraction_lab import (
    TYPE_PATTERNS,
    SourceChunk,
    _build_chunks,
    _coerce_value,
    _extract_labeled_value,
    _parse_plain_text,
    _parse_with_best_parser,
    _tokens,
)
from app.services.parsers.base import preview_text, resolve_input
from app.services.parsers.orchestrator import PARSERS


CASES: dict[str, ExtractionCase] = {}
SCHEMAS: dict[str, ExtractionSchema] = {}
PARSED_DOCUMENTS: dict[str, ParsedDocument] = {}
CASE_DOCUMENT_INPUTS: dict[str, str] = {}
JOBS: dict[str, ExtractionResult] = {}
REVIEW_ACTIONS: dict[str, list[ReviewAction]] = {}


def create_case(payload: CaseCreate) -> ExtractionCase:
    """Create a new Case record in local memory (mock database)."""
    case = ExtractionCase(case_id=_id("case"), title=payload.title, user_id=payload.user_id)
    CASES[case.case_id] = case
    return case


def get_case(case_id: str) -> ExtractionCase:
    """Retrieve an in-memory Case by ID, raising 404 if not found."""
    case = CASES.get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


def list_cases() -> list[ExtractionCase]:
    """List all in-memory Cases ordered by creation time."""
    return sorted(CASES.values(), key=lambda item: item.created_at, reverse=True)


def attach_upload_to_case(case_id: str, file_path: Path, mime_type: str, size_bytes: int) -> DocumentMetadata:
    """Attach an uploaded document reference to a Case in local memory (mock database)."""
    case = get_case(case_id)
    document_id = store.gen_id("doc-case")
    doc = DocumentMetadata(
        id=document_id,
        name=file_path.name,
        type=DocumentType.OTHER,
        source=DocumentSource.UPLOAD,
        mime_type=mime_type or "application/octet-stream",
        size_bytes=size_bytes,
        page_count=0,
        status=DocumentStatus.UPLOADED,
        tags=["case-upload", case_id],
        uploaded_at=utcnow(),
    )
    store.documents[document_id] = doc
    case.document_ids.append(document_id)
    case.updated_at = utcnow()
    CASE_DOCUMENT_INPUTS[document_id] = f"upload:{file_path.name}"
    return doc


def create_schema(payload: SchemaCreate) -> ExtractionSchema:
    result = validate_json_schema(payload.json_schema)
    if not result.valid:
        raise HTTPException(status_code=400, detail={"errors": result.errors})
    schema_id = _id("schema")
    hints = _merge_schema_hints(payload.json_schema, payload.field_hints)
    schema = ExtractionSchema(
        schema_id=schema_id,
        user_id=payload.user_id,
        name=payload.name,
        json_schema=payload.json_schema,
        field_hints=hints,
    )
    SCHEMAS[schema_id] = schema
    return schema


def update_schema(schema_id: str, payload: SchemaUpdate) -> ExtractionSchema:
    schema = get_schema(schema_id)
    data = schema.model_dump()
    if payload.name is not None:
        data["name"] = payload.name
    if payload.json_schema is not None:
        result = validate_json_schema(payload.json_schema)
        if not result.valid:
            raise HTTPException(status_code=400, detail={"errors": result.errors})
        data["json_schema"] = payload.json_schema
    if payload.field_hints is not None:
        data["field_hints"] = payload.field_hints
    data["field_hints"] = _merge_schema_hints(data["json_schema"], data["field_hints"])
    data["version"] = schema.version + 1
    data["updated_at"] = utcnow()
    updated = ExtractionSchema.model_validate(data)
    SCHEMAS[schema_id] = updated
    return updated


def get_schema(schema_id: str) -> ExtractionSchema:
    schema = SCHEMAS.get(schema_id)
    if not schema:
        raise HTTPException(status_code=404, detail="Schema not found")
    return schema


def list_schemas() -> list[ExtractionSchema]:
    return sorted(SCHEMAS.values(), key=lambda item: item.updated_at, reverse=True)


def validate_json_schema(schema: dict[str, Any]) -> SchemaValidationResult:
    errors: list[str] = []
    if schema.get("type") != "object":
        errors.append("Top-level JSON Schema must have type='object'.")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        errors.append("JSON Schema must include a non-empty properties object.")
    field_paths = list(properties.keys()) if isinstance(properties, dict) else []
    required = schema.get("required", [])
    if required and not isinstance(required, list):
        errors.append("required must be a list of field names.")
    elif isinstance(required, list):
        missing = [field for field in required if field not in field_paths]
        if missing:
            errors.append(f"required references unknown fields: {', '.join(missing)}")
    return SchemaValidationResult(valid=not errors, errors=errors, field_paths=field_paths)


def parse_case(case_id: str) -> list[ParsedDocument]:
    case = get_case(case_id)
    case.status = "parsing"
    parsed: list[ParsedDocument] = []
    for document_id in case.document_ids:
        parsed.append(parse_document(document_id, case_id))
    case.status = "indexed"
    case.updated_at = utcnow()
    return parsed


def parse_document(document_id: str, case_id: str | None = None) -> ParsedDocument:
    doc = store.documents.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if document_id in PARSED_DOCUMENTS:
        return PARSED_DOCUMENTS[document_id]

    input_id = CASE_DOCUMENT_INPUTS.get(document_id)
    if not input_id:
        raise HTTPException(status_code=422, detail="Document is not backed by a parser input")

    parser_id, parser_name, result = _parse_input(input_id)
    parsed = _to_parsed_document(
        result=result,
        document_id=document_id,
        case_id=case_id or _case_for_document(document_id) or "",
        filename=doc.name,
        mime_type=doc.mime_type,
        parser_name=parser_name,
    )
    PARSED_DOCUMENTS[document_id] = parsed
    doc.page_count = parsed.page_count
    doc.status = DocumentStatus.OCR_DONE
    doc.processed_at = utcnow()
    doc.confidence = _quality_confidence(parsed)
    return parsed


def search_case(case_id: str, payload: SearchRequest) -> list[SearchHit]:
    chunks = _case_chunks(case_id)
    query_tokens = _tokens(payload.query)
    hits: list[SearchHit] = []
    for chunk in chunks:
        score = len(query_tokens.intersection(_tokens(chunk.text))) * 2.0
        if payload.query.lower() in chunk.text.lower():
            score += 5.0
        if score <= 0:
            continue
        hits.append(SearchHit(score=score, evidence=_chunk_evidence(chunk)))
    hits.sort(key=lambda item: item.score, reverse=True)
    return hits[: payload.top_k]


def run_case_extraction(case_id: str, payload: ExtractionRequest) -> ExtractionResult:
    """Run data extraction on in-memory case documents using a basic BM25 keyword/regex strategy.

    Provides a fast, low-cost fallback or local test method. Uses simple string searches
    and regex patterns to resolve fields instead of running fully-fledged RAG or LLMs.
    """
    case = get_case(case_id)
    schema = get_schema(payload.schema_id)
    if not case.document_ids:
        raise HTTPException(status_code=400, detail="Case has no documents")

    case.status = "extracting"
    started = utcnow()
    fields: dict[str, FieldResult] = {}
    for field_path, field_schema in _schema_properties(schema.json_schema).items():
        hint = schema.field_hints.get(field_path) or _default_hint(field_path, field_schema)
        result = _extract_field_result(field_path, field_schema, hint, case_id, payload.max_evidence_per_field)
        fields[field_path] = result

    validation_errors = _validate_results(schema.json_schema, fields)
    for field_path, errors in validation_errors.items():
        if field_path in fields:
            fields[field_path].validation_errors.extend(errors)
            if fields[field_path].status == "validated":
                fields[field_path].status = "invalid"

    final_json = {
        path: result.value
        for path, result in fields.items()
        if result.status in {"validated", "human_corrected", "low_confidence"}
    }
    status = "needs_review" if any(result.status != "validated" for result in fields.values()) else "completed"
    job = ExtractionResult(
        job_id=_id("job"),
        case_id=case_id,
        schema_id=schema.schema_id,
        status=status,
        fields=fields,
        final_json=final_json,
        validation_report={
            "errors": validation_errors,
            "review_required_fields": [path for path, result in fields.items() if result.status != "validated"],
        },
        started_at=started,
        completed_at=utcnow(),
    )
    JOBS[job.job_id] = job
    REVIEW_ACTIONS[job.job_id] = []
    case.status = "needs_review" if status == "needs_review" else "completed"
    case.updated_at = utcnow()
    return job


def get_job(job_id: str) -> ExtractionResult:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Extraction job not found")
    return job


def review_payload(job_id: str) -> ReviewPayload:
    job = get_job(job_id)
    return ReviewPayload(
        job=job,
        review_required_fields=[path for path, result in job.fields.items() if result.status != "validated"],
        actions=REVIEW_ACTIONS.get(job_id, []),
    )


def approve_field(job_id: str, field_path: str, reviewer_id: str, reason: str | None) -> FieldResult:
    job = get_job(job_id)
    field = _job_field(job, field_path)
    field.status = "validated"
    field.validation_errors = []
    action = ReviewAction(
        review_id=_id("review"),
        job_id=job_id,
        field_path=field_path,
        action="approve",
        old_value=field.value,
        corrected_value=field.value,
        reviewer_id=reviewer_id,
        reason=reason,
    )
    REVIEW_ACTIONS.setdefault(job_id, []).append(action)
    _refresh_job_after_review(job)
    return field


def correct_field(job_id: str, field_path: str, corrected_value: Any, reviewer_id: str, reason: str | None) -> FieldResult:
    job = get_job(job_id)
    field = _job_field(job, field_path)
    old = field.value
    field.value = corrected_value
    field.status = "human_corrected"
    field.confidence = 1.0
    field.validation_errors = []
    field.candidates.append(
        FieldCandidate(
            candidate_id=_id("cand"),
            field_path=field_path,
            value=corrected_value,
            normalized_value=corrected_value,
            confidence=1.0,
            evidence=[],
            extraction_method="human",
        )
    )
    action = ReviewAction(
        review_id=_id("review"),
        job_id=job_id,
        field_path=field_path,
        action="correct",
        old_value=old,
        corrected_value=corrected_value,
        reviewer_id=reviewer_id,
        reason=reason,
    )
    REVIEW_ACTIONS.setdefault(job_id, []).append(action)
    _refresh_job_after_review(job)
    return field


def finalize_job(job_id: str) -> ExtractionResult:
    job = get_job(job_id)
    pending = [path for path, result in job.fields.items() if result.status in {"conflict", "missing", "invalid"}]
    if pending:
        raise HTTPException(status_code=409, detail={"pending_fields": pending})
    job.status = "completed"
    job.completed_at = utcnow()
    case = get_case(job.case_id)
    case.status = "completed"
    case.updated_at = utcnow()
    return job


def export_job(job_id: str) -> ExportBundle:
    job = get_job(job_id)
    parsed_docs = [PARSED_DOCUMENTS[doc_id] for doc_id in get_case(job.case_id).document_ids if doc_id in PARSED_DOCUMENTS]
    evidence_report = {
        path: [candidate.model_dump(mode="json") for candidate in result.candidates]
        for path, result in job.fields.items()
    }
    return ExportBundle(
        final_json=job.final_json,
        parsed_markdown="\n\n".join(_document_markdown(doc) for doc in parsed_docs),
        evidence_report=evidence_report,
        validation_report=job.validation_report,
        review_log=[action.model_dump(mode="json") for action in REVIEW_ACTIONS.get(job_id, [])],
    )


def write_export_files(job_id: str) -> dict[str, str]:
    bundle = export_job(job_id)
    out_dir = Path(settings.upload_dir).parent / "exports" / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "final.json": bundle.final_json,
        "parsed_markdown.md": bundle.parsed_markdown,
        "evidence_report.json": bundle.evidence_report,
        "validation_report.json": bundle.validation_report,
        "review_log.json": bundle.review_log,
    }
    paths: dict[str, str] = {}
    for name, content in files.items():
        path = out_dir / name
        if name.endswith(".md"):
            path.write_text(str(content), encoding="utf-8")
        else:
            path.write_text(json.dumps(content, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        paths[name] = str(path)
    return paths


def _parse_input(input_id: str) -> tuple[str, str, ParserRunResult]:
    input_info = resolve_input(input_id)
    if not input_info:
        raise HTTPException(status_code=404, detail="Parser input not found")
    if input_info.input_type == "text":
        return "plain_text", "Plain text", _parse_plain_text(Path(input_info.path), 12000)
    for parser_id in ["paddleocr_vl", "paddle_ocr", "layout_pdfplumber", "pdfplumber", "pymupdf", "pypdf", "docling", "unstructured", "pillow"]:
        module = PARSERS[parser_id]
        if input_info.input_type not in module.SUPPORTED_INPUT_TYPES or not module.is_available():
            continue
        result = module.parse(Path(input_info.path), preview_chars=12000)
        if result.status == ParserStatus.OK and (result.raw_text or result.text_preview).strip():
            return parser_id, module.DISPLAY_NAME, result
    payload = type("Payload", (), {"input_id": input_id, "parser_id": "auto", "preview_chars": 12000})()
    return _parse_with_best_parser(payload)


def _to_parsed_document(
    result: ParserRunResult,
    document_id: str,
    case_id: str,
    filename: str,
    mime_type: str,
    parser_name: str,
) -> ParsedDocument:
    chunks = _build_chunks(result, max(result.pages or 1, 500))
    pages: dict[int, list[SourceChunk]] = {}
    for chunk in chunks:
        pages.setdefault(chunk.page, []).append(chunk)
    parsed_pages: list[ParsedPage] = []
    tables: list[ParsedTable] = []
    for page_number in sorted(pages) or [1]:
        page_chunks = pages.get(page_number, [])
        blocks = [
            TextBlock(
                block_id=chunk.id,
                document_id=document_id,
                page_number=page_number,
                block_type="table" if chunk.type == "table" else "paragraph",
                text=chunk.text,
                bbox=chunk.bbox,
                reading_order=index,
            )
            for index, chunk in enumerate(page_chunks)
        ]
        text = "\n\n".join(block.text for block in blocks)
        parsed_pages.append(
            ParsedPage(
                document_id=document_id,
                page_number=page_number,
                text=text,
                markdown=text,
                blocks=blocks,
            )
        )
        for chunk in page_chunks:
            if chunk.type == "table":
                tables.append(
                    ParsedTable(
                        table_id=chunk.id,
                        document_id=document_id,
                        page_number=page_number,
                        markdown=chunk.text,
                    )
                )
    quality = _quality_label(parsed_pages)
    return ParsedDocument(
        document_id=document_id,
        case_id=case_id,
        filename=filename,
        mime_type=mime_type,
        parser_name=parser_name,
        page_count=max(result.pages, len(parsed_pages), 1),
        parse_quality=quality,
        pages=parsed_pages,
        tables=tables,
        metadata={"chars": result.chars, "seconds": result.seconds, "status": result.status.value},
    )


def _case_chunks(case_id: str) -> list[SourceChunk]:
    case = get_case(case_id)
    chunks: list[SourceChunk] = []
    for document_id in case.document_ids:
        parsed = parse_document(document_id, case_id)
        doc = store.documents[document_id]
        for page in parsed.pages:
            for block in page.blocks:
                chunks.append(
                    SourceChunk(
                        id=f"{document_id}:{block.block_id}",
                        page=page.page_number,
                        type=block.block_type,
                        text=block.text,
                        bbox=block.bbox if isinstance(block.bbox, dict) else None,
                    )
                )
                setattr(chunks[-1], "document_id", document_id)
                setattr(chunks[-1], "filename", doc.name)
    return chunks


def _extract_field_result(
    field_path: str,
    field_schema: dict[str, Any],
    hint: FieldExtractionHints,
    case_id: str,
    max_evidence: int,
) -> FieldResult:
    field_type = _field_type(field_schema)
    candidates: list[FieldCandidate] = []
    chunks = _rank_chunks(field_path, field_schema, hint, _case_chunks(case_id), max_evidence)
    for chunk, score in chunks:
        raw_value = _extract_value(field_path, field_type, chunk.text)
        if raw_value is None:
            continue
        normalized = _coerce_value(raw_value, field_type)
        if normalized is None and field_schema.get("type") != "string":
            continue
        confidence = min(0.98, 0.48 + (score / 20.0))
        candidates.append(
            FieldCandidate(
                candidate_id=_id("cand"),
                field_path=field_path,
                value=raw_value,
                normalized_value=normalized,
                confidence=round(confidence, 3),
                evidence=[_chunk_evidence(chunk, confidence=round(confidence, 3))],
                extraction_method="regex" if TYPE_PATTERNS.get(field_type) else "keyword_rule",
            )
        )

    if not candidates:
        return FieldResult(field_path=field_path, status="missing", candidates=[])

    selected, status = _resolve_candidates(candidates, hint)
    return FieldResult(
        field_path=field_path,
        value=selected.normalized_value if selected else None,
        status=status,
        confidence=selected.confidence if selected else 0.0,
        selected_candidate_id=selected.candidate_id if selected else None,
        candidates=candidates,
    )


def _rank_chunks(
    field_path: str,
    field_schema: dict[str, Any],
    hint: FieldExtractionHints,
    chunks: list[SourceChunk],
    limit: int,
) -> list[tuple[SourceChunk, float]]:
    query = " ".join([field_path, field_schema.get("description", ""), hint.description, " ".join(hint.keywords)])
    query_tokens = _tokens(query)
    ranked: list[tuple[SourceChunk, float]] = []
    for chunk in chunks:
        text_lower = chunk.text.lower()
        score = len(query_tokens.intersection(_tokens(chunk.text))) * 2.0
        score += sum(3.0 for keyword in hint.keywords if keyword.lower() in text_lower)
        if field_path.replace("_", " ").lower() in text_lower:
            score += 8.0
        pattern = TYPE_PATTERNS.get(_field_type(field_schema))
        if pattern and pattern.search(chunk.text):
            score += 2.0
        if score > 0:
            ranked.append((chunk, score))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:limit]


def _extract_value(field_path: str, field_type: ExtractionFieldType, text: str) -> Any:
    field = ExtractionSchemaField(key=field_path, label=field_path.replace("_", " "), type=field_type)
    labeled = _extract_labeled_value(field, text)
    if labeled is not None:
        return labeled
    pattern = TYPE_PATTERNS.get(field_type)
    if pattern:
        match = pattern.search(text)
        if match:
            return match.group(0)
    if field_type == ExtractionFieldType.TEXT:
        for line in text.splitlines():
            clean = line.strip()
            if clean:
                return preview_text(clean, 260)
    return None


def _resolve_candidates(candidates: list[FieldCandidate], hint: FieldExtractionHints) -> tuple[FieldCandidate | None, str]:
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    selected = candidates[0]
    values = {_normal_value(candidate.normalized_value) for candidate in candidates if candidate.normalized_value is not None}
    if len(values) > 1 and hint.conflict_policy == "human_review_on_disagreement":
        return None, "conflict"
    if selected.confidence < 0.7:
        return selected, "low_confidence"
    return selected, "validated"


def _validate_results(schema: dict[str, Any], fields: dict[str, FieldResult]) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    for field in schema.get("required", []) or []:
        if field not in fields or fields[field].value is None:
            errors.setdefault(field, []).append("Required field is missing")
    for field_path, field_schema in _schema_properties(schema).items():
        if field_path not in fields or fields[field_path].value is None:
            continue
        expected = field_schema.get("type", "string")
        value = fields[field_path].value
        if expected == "number" and not isinstance(value, (int, float)):
            errors.setdefault(field_path, []).append("Expected number")
        elif expected == "boolean" and not isinstance(value, bool):
            errors.setdefault(field_path, []).append("Expected boolean")
        elif expected == "array" and not isinstance(value, list):
            errors.setdefault(field_path, []).append("Expected array")
        elif expected == "object" and not isinstance(value, dict):
            errors.setdefault(field_path, []).append("Expected object")
    return errors


def _refresh_job_after_review(job: ExtractionResult) -> None:
    job.final_json = {
        path: result.value
        for path, result in job.fields.items()
        if result.status in {"validated", "human_corrected", "low_confidence"}
    }
    pending = [path for path, result in job.fields.items() if result.status in {"conflict", "missing", "invalid"}]
    job.validation_report["review_required_fields"] = pending
    job.status = "needs_review" if pending else "completed"
    job.completed_at = utcnow()


def _job_field(job: ExtractionResult, field_path: str) -> FieldResult:
    field = job.fields.get(field_path)
    if not field:
        raise HTTPException(status_code=404, detail="Field not found")
    return field


def _chunk_evidence(chunk: SourceChunk, confidence: float | None = None) -> EvidenceSource:
    document_id = getattr(chunk, "document_id", "")
    return EvidenceSource(
        evidence_id=chunk.id,
        document_id=document_id,
        filename=getattr(chunk, "filename", ""),
        page_number=chunk.page,
        source_type="table_row" if chunk.type == "table" else "text_block",
        text=preview_text(chunk.text, 600),
        bbox=chunk.bbox,
        confidence=confidence,
    )


def _schema_properties(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    properties = schema.get("properties", {})
    return properties if isinstance(properties, dict) else {}


def _merge_schema_hints(
    schema: dict[str, Any],
    hints: dict[str, FieldExtractionHints],
) -> dict[str, FieldExtractionHints]:
    merged: dict[str, FieldExtractionHints] = {}
    for field_path, field_schema in _schema_properties(schema).items():
        merged[field_path] = hints.get(field_path) or _default_hint(field_path, field_schema)
    return merged


def _default_hint(field_path: str, field_schema: dict[str, Any]) -> FieldExtractionHints:
    description = str(field_schema.get("description") or "")
    words = [field_path, field_path.replace("_", " "), description]
    return FieldExtractionHints(
        field_path=field_path,
        description=description,
        keywords=sorted(_tokens(" ".join(words))),
        value_type=str(field_schema.get("format") or field_schema.get("type") or "text"),
    )


def _field_type(field_schema: dict[str, Any]) -> ExtractionFieldType:
    schema_type = field_schema.get("type")
    schema_format = field_schema.get("format")
    if schema_format == "date":
        return ExtractionFieldType.DATE
    if schema_type in {"number", "integer"}:
        return ExtractionFieldType.NUMBER
    if schema_type == "boolean":
        return ExtractionFieldType.BOOLEAN
    if schema_type == "array":
        return ExtractionFieldType.LIST
    if schema_type == "object":
        return ExtractionFieldType.OBJECT
    return ExtractionFieldType.TEXT


def _case_for_document(document_id: str) -> str | None:
    for case in CASES.values():
        if document_id in case.document_ids:
            return case.case_id
    return None


def _document_markdown(doc: ParsedDocument) -> str:
    parts = [f"# {doc.filename}"]
    for page in doc.pages:
        parts.append(f"<!-- page: {page.page_number} -->\n{page.markdown or page.text}")
    return "\n\n".join(parts)


def _quality_label(pages: list[ParsedPage]) -> str:
    if not pages:
        return "poor"
    avg = sum(len(page.text or "") for page in pages) / len(pages)
    empty = sum(1 for page in pages if not (page.text or "").strip())
    if empty or avg < 80:
        return "poor"
    if avg < 300:
        return "medium"
    return "good"


def _quality_confidence(parsed: ParsedDocument) -> float:
    return {"good": 0.92, "medium": 0.78, "poor": 0.45}.get(parsed.parse_quality or "", 0.5)


def _normal_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
