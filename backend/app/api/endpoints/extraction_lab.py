"""API endpoints for the schema-driven Extraction Lab."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.config import settings
from app.db.engine import get_factory, is_db_configured
from app.models.extraction_lab import (
    ExtractionLabSchema,
    ExtractionLabSchemaTemplate,
    MultiDocumentExtractionRunRequest,
    MultiDocumentExtractionRunResponse,
    MultiDocumentMode,
    ExtractionReportRequest,
    ExtractionReportResponse,
    ExtractionRunRequest,
    ExtractionRunResponse,
    SchemaGenerationRequest,
    SchemaGenerationResponse,
)
from app.models.parser_benchmark import ParserInfo, ParserInputInfo
from app.services.extraction_lab import AUTO_PARSER_ORDER, generate_polished_report, generate_schema_definition, list_schema_templates, run_extraction, run_extraction_db, run_multi_document_extraction_db, save_schema_template
from app.services.parsers.base import input_type_for, list_parser_inputs, page_count_for
from app.services.parsers.orchestrator import list_parsers

router = APIRouter(prefix="/extraction-lab", tags=["extraction-lab"])


@router.get("/inputs", response_model=list[ParserInputInfo])
async def inputs():
    return list_parser_inputs()


@router.get("/parsers", response_model=list[ParserInfo])
async def parsers():
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
    return list_schema_templates()


@router.post("/schemas", response_model=ExtractionLabSchemaTemplate)
async def save_schema(payload: ExtractionLabSchema):
    return save_schema_template(payload.name, payload)


@router.post("/upload", response_model=ParserInputInfo)
async def upload_input(file: UploadFile = File(...)):
    return await _save_upload(file)


@router.post("/upload-multiple", response_model=list[ParserInputInfo])
async def upload_inputs(files: list[UploadFile] = File(...)):
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
    if is_db_configured():
        async with get_factory()() as session:
            return await run_extraction_db(session, payload)
    return run_extraction(payload)


@router.post("/run-multi", response_model=MultiDocumentExtractionRunResponse)
async def run_multi(payload: MultiDocumentExtractionRunRequest):
    if is_db_configured():
        async with get_factory()() as session:
            return await run_multi_document_extraction_db(session, payload)
    if payload.multi_document_mode != MultiDocumentMode.PER_DOCUMENT:
        raise HTTPException(status_code=422, detail="Cross-document extraction requires the DB-backed evidence index")
    results = [
        run_extraction(ExtractionRunRequest(**{**payload.model_dump(mode="python"), "input_id": input_id}))
        for input_id in dict.fromkeys(payload.input_ids)
    ]
    return MultiDocumentExtractionRunResponse(mode=payload.multi_document_mode, results=results)


@router.post("/generate-schema", response_model=SchemaGenerationResponse)
async def generate_schema(payload: SchemaGenerationRequest):
    return generate_schema_definition(payload)


@router.post("/report", response_model=ExtractionReportResponse)
async def report(payload: ExtractionReportRequest):
    return ExtractionReportResponse(report_markdown=generate_polished_report(payload.result))


@router.get("/results/{input_id}", response_model=list[ExtractionRunResponse])
async def get_results(input_id: str):
    if is_db_configured():
        async with get_factory()() as session:
            from sqlalchemy import select
            from app.db.models import ExtractionResultModel
            stmt = select(ExtractionResultModel).where(ExtractionResultModel.input_id == input_id).order_by(ExtractionResultModel.created_at.desc())
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [ExtractionRunResponse.model_validate(row.response_json) for row in rows]
    return []
