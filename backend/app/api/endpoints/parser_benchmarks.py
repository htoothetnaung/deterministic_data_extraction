"""API endpoints for parser benchmark comparisons."""
from __future__ import annotations

import mimetypes
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse, PlainTextResponse

from app.models.parser_benchmark import (
    ParserCorrection,
    ParserGroundTruth,
    ParserInfo,
    ParserInputInfo,
    ParserResultDetail,
    ParserRunRequest,
    ParserRunResponse,
    ParserRunSummary,
)
from app.services.parsers.base import list_parser_inputs, resolve_input
from app.services.parsers.orchestrator import list_parsers, run_parser_benchmark
from app.services.parsers.persistence import (
    get_ground_truth,
    get_cleaned_evidence,
    get_result_detail,
    get_run,
    list_runs,
    output_root,
    save_corrections,
    save_ground_truth,
)

router = APIRouter(prefix="/parser-benchmarks", tags=["parser-benchmarks"])


@router.get("/inputs", response_model=list[ParserInputInfo])
async def inputs():
    return list_parser_inputs()


@router.get("/preview/{input_id:path}")
async def preview_input(input_id: str):
    item = resolve_input(input_id)
    if not item:
        raise HTTPException(status_code=404, detail="Parser input not found")
    path = Path(item.path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Parser input file not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
    )


@router.get("/preview-text/{input_id:path}", response_class=PlainTextResponse)
async def preview_input_text(input_id: str):
    item = resolve_input(input_id)
    if not item:
        raise HTTPException(status_code=404, detail="Parser input not found")
    path = Path(item.path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Parser input file not found")
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _docx_text(path)
    if suffix in {".txt", ".md", ".csv", ".tsv", ".json"}:
        return path.read_text(encoding="utf-8", errors="replace")
    raise HTTPException(status_code=400, detail="Text preview is not available for this file type")


@router.get("/preview-page/{input_id:path}")
async def preview_input_page(
    input_id: str,
    page: int = Query(default=1, ge=1),
    zoom: float = Query(default=1.4, ge=0.5, le=4.0),
):
    item = resolve_input(input_id)
    if not item:
        raise HTTPException(status_code=404, detail="Parser input not found")
    path = Path(item.path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Parser input file not found")

    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(
            path,
            media_type=media_type,
            filename=path.name,
            content_disposition_type="inline",
        )
    if suffix != ".pdf":
        raise HTTPException(status_code=400, detail="Page preview is only available for PDF and image inputs")

    try:
        import fitz
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="Install pymupdf to enable PDF page previews") from exc

    with fitz.open(str(path)) as document:
        if page > document.page_count:
            raise HTTPException(status_code=404, detail="PDF page not found")
        pdf_page = document.load_page(page - 1)
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = pdf_page.get_pixmap(matrix=matrix, alpha=False)
        return Response(
            content=pixmap.tobytes("png"),
            media_type="image/png",
            headers={
                "Content-Disposition": f'inline; filename="{path.stem}-page-{page}.png"',
                "Cache-Control": "public, max-age=300",
            },
        )


@router.get("/media/{media_path:path}")
async def parser_media(media_path: str):
    media_root = (output_root() / "media").resolve()
    path = (media_root / media_path).resolve()
    if not path.is_file() or not path.is_relative_to(media_root):
        raise HTTPException(status_code=404, detail="Parser media not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
    )


@router.get("/parsers", response_model=list[ParserInfo])
async def parsers():
    return list_parsers()


@router.get("/runs", response_model=list[ParserRunSummary])
async def runs():
    return list_runs()


@router.post("/run", response_model=ParserRunResponse)
async def run(payload: ParserRunRequest):
    return run_parser_benchmark(payload)


@router.get("/runs/{run_id}", response_model=ParserRunResponse)
async def run_detail(run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Parser run not found")
    return run


@router.get("/runs/{run_id}/results/{library}", response_model=ParserResultDetail)
async def result_detail(run_id: str, library: str):
    detail = get_result_detail(run_id, library)
    if not detail:
        raise HTTPException(status_code=404, detail="Parser result not found")
    return detail


@router.get("/runs/{run_id}/results/{library}/cleaned-evidence")
async def cleaned_evidence(run_id: str, library: str):
    payload = get_cleaned_evidence(run_id, library)
    if payload is None:
        raise HTTPException(status_code=404, detail="Parser result not found")
    return payload


@router.get("/ground-truth/{input_id:path}", response_model=ParserGroundTruth)
async def ground_truth(input_id: str):
    return get_ground_truth(input_id)


@router.put("/ground-truth/{input_id:path}", response_model=ParserGroundTruth)
async def update_ground_truth(input_id: str, payload: ParserGroundTruth):
    return save_ground_truth(input_id, payload)


@router.put("/runs/{run_id}/results/{library}/corrections", response_model=ParserCorrection)
async def update_corrections(run_id: str, library: str, payload: ParserCorrection):
    saved = save_corrections(run_id, library, payload)
    if not saved:
        raise HTTPException(status_code=404, detail="Parser result not found")
    return saved


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [
            text.text or ""
            for text in paragraph.findall(".//w:t", namespace)
            if text.text
        ]
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs)
