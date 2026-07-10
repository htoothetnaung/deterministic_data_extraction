"""Parser benchmark orchestration."""
from __future__ import annotations

import time
import logging
from pathlib import Path
from typing import Protocol

from fastapi import HTTPException

from app.models.document import utcnow
from app.models.parser_benchmark import ParserInfo, ParserRunRequest, ParserRunResponse, ParserRunResult
from app.services.parsers import mistral_ocr
from app.services.parsers.base import failed_result, resolve_input
from app.services.parsers.persistence import new_run_id, persist_run

logger = logging.getLogger("uvicorn.error")


class ParserModule(Protocol):
    LIBRARY_ID: str
    DISPLAY_NAME: str
    SUPPORTED_INPUT_TYPES: list[str]

    def is_available(self) -> bool: ...

    def parse(self, input_path: Path, preview_chars: int = 1500) -> ParserRunResult: ...


PARSERS: dict[str, ParserModule] = {
    module.LIBRARY_ID: module
    for module in (mistral_ocr,)
}


def list_parsers() -> list[ParserInfo]:
    items: list[ParserInfo] = []
    for parser_id, module in PARSERS.items():
        installed = module.is_available()
        notes_fn = getattr(module, "availability_notes", None)
        notes = notes_fn() if callable(notes_fn) else None
        if not notes and not installed:
            notes = f"Install dependency for {module.DISPLAY_NAME}."
        items.append(
            ParserInfo(
                id=parser_id,
                name=module.DISPLAY_NAME,
                supported_input_types=module.SUPPORTED_INPUT_TYPES,
                installed=installed,
                notes=notes,
            )
        )
    return items


def run_parser_benchmark(payload: ParserRunRequest) -> ParserRunResponse:
    input_info = resolve_input(payload.input_id)
    if not input_info:
        logger.warning("[stress-lab] input not found input_id=%s", payload.input_id)
        raise HTTPException(status_code=404, detail="Parser benchmark input not found")

    selected_ids = payload.parsers or list(PARSERS.keys())
    unknown = [parser_id for parser_id in selected_ids if parser_id not in PARSERS]
    if unknown:
        logger.warning("[stress-lab] unknown parser(s) input_id=%s parsers=%s", payload.input_id, unknown)
        raise HTTPException(status_code=400, detail=f"Unknown parser(s): {', '.join(unknown)}")

    input_path = Path(input_info.path)
    started_at = utcnow()
    run_id = new_run_id()
    run_started = time.perf_counter()
    results: list[ParserRunResult] = []
    logger.info(
        "[stress-lab] run started input=%s type=%s size_bytes=%s parsers=%s preview_chars=%s",
        input_info.name,
        input_info.input_type,
        input_info.size_bytes,
        ",".join(selected_ids),
        payload.preview_chars,
    )
    for parser_id in selected_ids:
        module = PARSERS[parser_id]
        started = time.perf_counter()
        logger.info("[stress-lab] parser started parser=%s input=%s", parser_id, input_info.name)
        try:
            result = module.parse(input_path, preview_chars=payload.preview_chars)
            result = result.model_copy(
                update={
                    "run_id": run_id,
                    "result_id": f"{run_id}:{parser_id}",
                }
            )
            results.append(result)
            logger.info(
                "[stress-lab] parser finished parser=%s status=%s seconds=%.3f pages=%s chars=%s tables=%s images=%s",
                parser_id,
                result.status.value,
                result.seconds,
                result.pages,
                result.chars,
                result.tables,
                result.images,
            )
        except Exception as exc:
            logger.exception("[stress-lab] parser failed parser=%s input=%s", parser_id, input_info.name)
            result = failed_result(
                parser_id,
                input_path,
                exc,
                seconds=time.perf_counter() - started,
                preview_chars=payload.preview_chars,
            )
            result = result.model_copy(
                update={
                    "run_id": run_id,
                    "result_id": f"{run_id}:{parser_id}",
                }
            )
            results.append(result)

    logger.info(
        "[stress-lab] run finished input=%s parsers=%s total_seconds=%.3f ok=%s skipped=%s failed=%s",
        input_info.name,
        len(selected_ids),
        time.perf_counter() - run_started,
        sum(1 for result in results if result.status.value == "ok"),
        sum(1 for result in results if result.status.value == "skipped"),
        sum(1 for result in results if result.status.value == "failed"),
    )

    run = ParserRunResponse(
        run_id=run_id,
        input=input_info,
        results=results,
        started_at=started_at,
        finished_at=utcnow(),
    )
    return persist_run(run)
