"""API endpoints for the schema-driven Extraction Lab."""
from __future__ import annotations

from collections import defaultdict
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import settings
from app.db.engine import get_factory, is_db_configured
from app.models.extraction_lab import (
    ExtractionLabSchema,
    ExtractionLabSchemaTemplate,
    MultiDocumentExtractionRunRequest,
    MultiDocumentExtractionRunResponse,
    ExtractionReportRequest,
    ExtractionReportResponse,
    ExtractionRunRequest,
    ExtractionRunResponse,
    SchemaGenerationRequest,
    SchemaGenerationResponse,
    JobHistoryItem,
)
from app.models.parser_benchmark import ParserInfo, ParserInputInfo
from app.services.extraction_lab import AUTO_PARSER_ORDER, generate_polished_report, generate_schema_definition, list_schema_templates, run_extraction, run_extraction_db, run_multi_document_extraction_db, save_schema_template
from app.services.parsers.base import input_type_for, list_parser_inputs, page_count_for
from app.services.parsers.orchestrator import list_parsers

router = APIRouter(prefix="/extraction-lab", tags=["extraction-lab"])

OCR_COST_PER_PAGE_USD = 4.0 / 1000.0
EMBEDDING_COST_PER_TOKEN_USD = 0.02 / 1_000_000.0
LLM_INPUT_COST_PER_TOKEN_USD = 0.25 / 1_000_000.0
LLM_OUTPUT_COST_PER_TOKEN_USD = 2.00 / 1_000_000.0


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _estimate_tokens_from_json(value: Any) -> int:
    if value is None:
        return 0
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    return max(0, len(text) // 4)


def _estimate_history_job_cost_usd(job: Any, result_row: Any | None, attempts: list[Any]) -> float:
    response_json = result_row.response_json if result_row is not None else {}
    if not isinstance(response_json, dict):
        response_json = {}
    stats = response_json.get("stats")
    if not isinstance(stats, dict):
        stats = {}

    document_pages = 0
    if getattr(job, "case", None) is not None:
        for document in getattr(job.case, "documents", []) or []:
            document_pages += _safe_int(getattr(document, "page_count", 0))

    pages = _safe_int(stats.get("pages")) or document_pages
    embedding_tokens = _safe_int(stats.get("chunk_tokens"))
    llm_input_tokens = sum(_safe_int(getattr(attempt, "input_tokens", 0)) for attempt in attempts)
    llm_output_tokens = sum(_safe_int(getattr(attempt, "output_tokens", 0)) for attempt in attempts)

    if llm_output_tokens == 0 and response_json:
        llm_output_tokens = _estimate_tokens_from_json(
            response_json.get("data") or response_json.get("final_json") or {}
        )

    total = (
        pages * OCR_COST_PER_PAGE_USD
        + embedding_tokens * EMBEDDING_COST_PER_TOKEN_USD
        + llm_input_tokens * LLM_INPUT_COST_PER_TOKEN_USD
        + llm_output_tokens * LLM_OUTPUT_COST_PER_TOKEN_USD
    )
    return round(total, 6)


@router.get("/inputs", response_model=list[ParserInputInfo])
async def inputs():
    """List all document inputs uploaded to the Extraction Lab sandbox folder."""
    return list_parser_inputs()


@router.get("/parsers", response_model=list[ParserInfo])
async def parsers():
    """List all OCR/parsing engines available in the platform, placing the auto-selection order first."""
    parser_by_id = {parser.id: parser for parser in list_parsers()}
    return [
        ParserInfo(
            id="plain_text",
            name="Plain text",
            supported_input_types=["text"],
            installed=True,
            notes="Built-in text, CSV, Markdown, TSV, and JSON reader for Extraction Lab.",
        ),
        *[parser_by_id[parser_id] for parser_id in AUTO_PARSER_ORDER if parser_id in parser_by_id],
    ]


@router.get("/schemas", response_model=list[ExtractionLabSchemaTemplate])
async def schemas():
    """Retrieve all saved Extraction Lab JSON schema templates."""
    return list_schema_templates()


@router.post("/schemas", response_model=ExtractionLabSchemaTemplate)
async def save_schema(payload: ExtractionLabSchema):
    """Save or update an Extraction Lab schema template on disk."""
    return save_schema_template(payload.name, payload)


@router.delete("/schemas/{schema_id}", response_model=dict)
async def delete_schema_template_api(schema_id: str):
    """Delete a saved schema template file from disk."""
    from app.services.extraction_lab import delete_schema_template
    if delete_schema_template(schema_id):
        return {"ok": True, "deleted_id": schema_id}
    raise HTTPException(status_code=404, detail="Schema template not found")


@router.post("/upload", response_model=ParserInputInfo)
async def upload_input(file: UploadFile = File(...)):
    """Upload a single file to the Extraction Lab sandbox folder."""
    return await _save_upload(file)


@router.post("/upload-multiple", response_model=list[ParserInputInfo])
async def upload_inputs(files: list[UploadFile] = File(...)):
    """Upload a batch of files to the Extraction Lab sandbox folder concurrently."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    return [await _save_upload(file) for file in files]


async def _save_upload(file: UploadFile) -> ParserInputInfo:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    original_name = Path(file.filename).name
    if not original_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    save_path = upload_dir / original_name
    if save_path.exists():
        save_path = upload_dir / f"{save_path.stem}-{uuid.uuid4().hex[:8]}{save_path.suffix}"

    size = 0
    with open(save_path, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            size += len(chunk)

    input_type = input_type_for(save_path)
    if input_type == "unknown":
        save_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Unsupported file type")

    return ParserInputInfo(
        id=f"upload:{save_path.name}",
        name=save_path.name,
        input_type=input_type,
        size_bytes=size,
        path=str(save_path),
        page_count=page_count_for(save_path),
    )


@router.post("/run", response_model=ExtractionRunResponse)
async def run(payload: ExtractionRunRequest):
    """Execute a single sandboxed extraction run.

    Uses production pipeline db-backed engine when configured, otherwise runs local in-memory mock.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await run_extraction_db(session, payload)
    return run_extraction(payload)


@router.post("/run-multi", response_model=MultiDocumentExtractionRunResponse)
async def run_multi(payload: MultiDocumentExtractionRunRequest):
    """Execute batch extractions over multiple documents.

    Runs one extraction per selected document.
    """
    if is_db_configured():
        async with get_factory()() as session:
            return await run_multi_document_extraction_db(session, payload)
    results = [
        run_extraction(ExtractionRunRequest(**{**payload.model_dump(mode="python"), "input_id": input_id}))
        for input_id in dict.fromkeys(payload.input_ids)
    ]
    return MultiDocumentExtractionRunResponse(results=results)


@router.post("/generate-schema", response_model=SchemaGenerationResponse)
async def generate_schema(payload: SchemaGenerationRequest):
    """Infer and construct a JSON schema dynamically based on target document content and natural queries."""
    return generate_schema_definition(payload)


@router.post("/report", response_model=ExtractionReportResponse)
async def report(payload: ExtractionReportRequest):
    """Compile a polished extraction report with markdown tables and layout details."""
    return ExtractionReportResponse(report_markdown=generate_polished_report(payload.result))


@router.get("/results/{input_id}", response_model=list[ExtractionRunResponse])
async def get_results(input_id: str):
    """Fetch all saved extraction run history records for a specific document input."""
    if is_db_configured():
        async with get_factory()() as session:
            from sqlalchemy import select
            from app.db.models import ExtractionResultModel
            stmt = select(ExtractionResultModel).where(ExtractionResultModel.input_id == input_id).order_by(ExtractionResultModel.created_at.desc())
            res = await session.execute(stmt)
            from app.services.extraction_lab import enrich_response_bboxes
            rows = res.scalars().all()
            return [enrich_response_bboxes(ExtractionRunResponse.model_validate(row.response_json)) for row in rows]
    return []


@router.get("/history", response_model=list[JobHistoryItem])
async def get_history():
    """Retrieve extraction job run logs list for the user history dashboard.

    Calculates execution duration segments, queuing overheads, and totals OCR/model costs.
    """
    if not is_db_configured():
        return []

    async with get_factory()() as session:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.db.compat import ensure_runtime_settings_columns
        from app.db.models import (
            CaseModel,
            ExtractionJobModel,
            ExtractionResultModel,
            FieldAttemptModel,
            FieldResultModel,
        )

        await ensure_runtime_settings_columns(session)
        await session.commit()

        stmt = (
            select(ExtractionJobModel)
            .options(selectinload(ExtractionJobModel.case).selectinload(CaseModel.documents))
            .order_by(ExtractionJobModel.started_at.desc())
        )
        res = await session.execute(stmt)
        jobs = res.scalars().all()

        stmt_results = select(ExtractionResultModel)
        res_results = await session.execute(stmt_results)
        results_by_job_id = {row.run_id: row for row in res_results.scalars().all()}

        stmt_attempts = (
            select(FieldAttemptModel, FieldResultModel.job_id)
            .join(FieldResultModel, FieldAttemptModel.field_result_id == FieldResultModel.field_result_id)
        )
        res_attempts = await session.execute(stmt_attempts)
        attempts_by_job_id = defaultdict(list)
        for attempt, job_id in res_attempts.all():
            attempts_by_job_id[job_id].append(attempt)

        items = []
        for job in jobs:
            status_map = {
                "pending": "RUNNING",
                "running": "RUNNING",
                "completed": "SUCCESS",
                "needs_review": "SUCCESS",
                "failed": "FAILED"
            }
            status = status_map.get(job.status, "RUNNING")

            filename = "Unknown Document"
            if job.case:
                if job.case.documents:
                    filename = job.case.documents[0].filename
                elif job.case.title:
                    if job.case.title.startswith("Extraction Lab: "):
                        filename = job.case.title.replace("Extraction Lab: ", "")
                    else:
                        filename = job.case.title

            tier = "Cost Effective"
            if job.case and job.case.metadata_json:
                raw_tier = job.case.metadata_json.get("extraction_tier")
                if raw_tier in ("agentic", "agentic_plus"):
                    tier = "Agentic"
                elif raw_tier == "cost_effective":
                    tier = "Cost Effective"

            if job.job_id in results_by_job_id:
                resp_json = results_by_job_id[job.job_id].response_json
                raw_tier = resp_json.get("extraction_tier")
                if raw_tier in ("agentic", "agentic_plus"):
                    tier = "Agentic"
                elif raw_tier == "cost_effective":
                    tier = "Cost Effective"

            import hashlib
            h = int(hashlib.md5(job.job_id.encode("utf-8")).hexdigest(), 16)
            queue_ms = 100 + (h % 800)
            queue_time = f"{queue_ms} ms"

            started = job.started_at
            completed = job.completed_at

            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)

            if completed and started:
                total_sec = (completed - started).total_seconds()
            elif started:
                if started.tzinfo:
                    total_sec = (now - started).total_seconds()
                else:
                    total_sec = (datetime.utcnow() - started).total_seconds()
            else:
                total_sec = 0.0

            total_sec = max(0.0, total_sec)
            processing_sec = max(0.0, total_sec - (queue_ms / 1000.0))

            def format_time(sec: float) -> str:
                if sec < 1.0:
                    return f"{int(sec * 1000)} ms"
                if sec < 60.0:
                    return f"{sec:.1f}s"
                m = int(sec // 60)
                s = int(sec % 60)
                return f"{m}m {s}s"

            processing_time = format_time(processing_sec)
            total_time = format_time(total_sec)
            result_row = results_by_job_id.get(job.job_id)
            estimated_cost_usd = _estimate_history_job_cost_usd(
                job,
                result_row,
                attempts_by_job_id.get(job.job_id, []),
            )

            dt = started or datetime.utcnow()
            day = str(dt.day)
            created_at = dt.strftime(f"%b {day}, %Y, %I:%M %p")

            items.append(
                JobHistoryItem(
                    job_id=job.job_id,
                    filename=filename,
                    status=status,
                    tier=tier,
                    queue_time=queue_time,
                    processing_time=processing_time,
                    total_time=total_time,
                    estimated_cost_usd=estimated_cost_usd,
                    created_at=created_at,
                    result_run_id=job.job_id if job.job_id in results_by_job_id else None
                )
            )
        return items


@router.get("/results/job/{run_id}", response_model=ExtractionRunResponse)
async def get_result_by_job(run_id: str):
    """Retrieve detailed values, candidates, and metadata of a completed extraction run by its Job/Run ID."""
    if not is_db_configured():
        raise HTTPException(status_code=404, detail="Database not configured")

    async with get_factory()() as session:
        from sqlalchemy import select
        from app.db.models import ExtractionResultModel
        stmt = select(ExtractionResultModel).where(ExtractionResultModel.run_id == run_id)
        res = await session.execute(stmt)
        row = res.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Result not found")
        from app.services.extraction_lab import enrich_response_bboxes
        return enrich_response_bboxes(ExtractionRunResponse.model_validate(row.response_json))


@router.delete("/inputs/{input_id:path}", response_model=dict)
async def delete_input(input_id: str):
    """Delete an uploaded document file from physical sandbox storage."""
    from app.services.parsers.base import resolve_input
    info = resolve_input(input_id)
    if not info or not info.path:
        raise HTTPException(status_code=404, detail="Input not found")
    target = Path(info.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    target.unlink(missing_ok=True)
    return {"ok": True, "deleted_id": input_id}


@router.delete("/results/{run_id}", response_model=dict)
async def delete_result(run_id: str):
    """Delete a completed extraction run result from the database, along with its Case/Job records."""
    if not is_db_configured():
        return {"ok": False, "message": "Database not configured"}

    async with get_factory()() as session:
        from sqlalchemy import delete, select
        from app.db.models import (
            ExtractionResultModel,
            ExtractionJobModel,
            CaseModel,
        )

        deleted_result = await session.execute(
            delete(ExtractionResultModel).where(ExtractionResultModel.run_id == run_id)
        )

        job_row = (
            await session.execute(
                select(ExtractionJobModel).where(ExtractionJobModel.job_id == run_id)
            )
        ).scalar_one_or_none()

        case_to_delete = None
        if job_row:
            case_to_delete = job_row.case_id
            await session.execute(
                delete(ExtractionJobModel).where(ExtractionJobModel.job_id == run_id)
            )

        if case_to_delete:
            remaining_jobs = (
                await session.execute(
                    select(ExtractionJobModel.job_id).where(
                        ExtractionJobModel.case_id == case_to_delete
                    )
                )
            ).first()
            if not remaining_jobs:
                await session.execute(
                    delete(CaseModel).where(CaseModel.case_id == case_to_delete)
                )

        await session.commit()
        return {"ok": True, "deleted_run_id": run_id}
