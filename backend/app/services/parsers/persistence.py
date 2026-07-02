"""Local file persistence for Stress Lab parser runs."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.document import utcnow
from app.models.parser_benchmark import (
    ParserArtifactPaths,
    ParserCorrection,
    ParserGroundTruth,
    ParserQualityCheck,
    ParserResultDetail,
    ParserRunResponse,
    ParserRunResult,
    ParserRunSummary,
    ParserStatus,
)
from app.services.parsers.base import project_root, resolve_input
from app.services.evidence_cleaner import clean_parser_result


def output_root() -> Path:
    return project_root() / "backend" / "parser_outputs"


def runs_root() -> Path:
    return output_root() / "runs"


def ground_truth_root() -> Path:
    return output_root() / "ground_truth"


def new_run_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"prun-{stamp}-{uuid.uuid4().hex[:8]}"


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._") or "item"


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )


def ground_truth_path(input_id: str) -> Path:
    return ground_truth_root() / f"{safe_name(input_id)}.json"


def get_ground_truth(input_id: str) -> ParserGroundTruth:
    input_info = resolve_input(input_id)
    data = _read_json(ground_truth_path(input_id), None)
    if data:
        return ParserGroundTruth.model_validate(data)
    return ParserGroundTruth(
        input_id=input_id,
        input_name=input_info.name if input_info else input_id,
    )


def save_ground_truth(input_id: str, payload: ParserGroundTruth) -> ParserGroundTruth:
    input_info = resolve_input(input_id)
    saved = payload.model_copy(
        update={
            "input_id": input_id,
            "input_name": input_info.name if input_info else payload.input_name,
            "updated_at": utcnow(),
        }
    )
    _write_json(ground_truth_path(input_id), saved.model_dump(mode="json"))
    return saved


def persist_run(run: ParserRunResponse) -> ParserRunResponse:
    run_dir = runs_root() / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    persisted_results: list[ParserRunResult] = []
    for result in run.results:
        library_dir = run_dir / safe_name(result.library)
        library_dir.mkdir(parents=True, exist_ok=True)
        output_path = library_dir / "output.md"
        structured_path = library_dir / "structured.json"
        corrections_path = library_dir / "corrections.json"

        output_text = result.raw_text or result.text_preview or result.error or ""
        output_path.write_text(output_text, encoding="utf-8")

        if not corrections_path.exists():
            _write_json(corrections_path, ParserCorrection().model_dump(mode="json"))

        persisted_result = result.model_copy(
            update={
                "run_id": run.run_id,
                "result_id": f"{run.run_id}:{result.library}",
                "artifact_paths": ParserArtifactPaths(
                    output_md=str(output_path),
                    structured_json=str(structured_path),
                    corrections_json=str(corrections_path),
                ),
            }
        )
        _write_json(structured_path, _structured_payload(persisted_result))
        persisted_results.append(persisted_result)

    persisted = run.model_copy(update={"results": persisted_results})
    _write_json(run_dir / "run.json", persisted.model_dump(mode="json"))
    return persisted


def list_runs() -> list[ParserRunSummary]:
    summaries: list[ParserRunSummary] = []
    for run_file in runs_root().glob("*/run.json"):
        try:
            run = ParserRunResponse.model_validate(_read_json(run_file, {}))
            summaries.append(_summary(run))
        except Exception:
            continue
    summaries.sort(key=lambda item: item.started_at, reverse=True)
    return summaries


def get_run(run_id: str) -> ParserRunResponse | None:
    path = runs_root() / safe_name(run_id) / "run.json"
    if not path.exists():
        return None
    return ParserRunResponse.model_validate(_read_json(path, {}))


def get_result_detail(run_id: str, library: str) -> ParserResultDetail | None:
    run = get_run(run_id)
    if not run:
        return None
    result = next((item for item in run.results if item.library == library), None)
    if not result:
        return None

    output_path = Path(result.artifact_paths.output_md) if result.artifact_paths.output_md else None
    corrections_path = Path(result.artifact_paths.corrections_json) if result.artifact_paths.corrections_json else None
    full_text = output_path.read_text(encoding="utf-8") if output_path and output_path.exists() else ""
    corrections = ParserCorrection.model_validate(_read_json(corrections_path, {}) if corrections_path else {})
    ground_truth = get_ground_truth(run.input.id)
    checks = score_ground_truth(full_text, ground_truth)
    confidence = _overall_confidence(checks, result.status)
    result = result.model_copy(
        update={
            "structured_preview": {
                **result.structured_preview,
                "heuristic_confidence": confidence,
                "quality_checks": [check.model_dump(mode="json") for check in checks],
            }
        }
    )
    return ParserResultDetail(
        run=run,
        result=result,
        full_text=full_text,
        ground_truth=ground_truth,
        corrections=corrections,
        quality_checks=checks,
    )


def get_latest_ok_result_for_input(input_id: str, library: str) -> tuple[ParserRunResponse, ParserRunResult] | None:
    latest: tuple[ParserRunResponse, ParserRunResult] | None = None
    for run_file in runs_root().glob("*/run.json"):
        try:
            run = ParserRunResponse.model_validate(_read_json(run_file, {}))
        except Exception:
            continue
        if run.input.id != input_id:
            continue
        result = next((item for item in run.results if item.library == library and item.status == ParserStatus.OK), None)
        if not result:
            continue
        if latest is None or run.started_at > latest[0].started_at:
            latest = (run, _rehydrate_result_text(result))
    return latest


def get_latest_ok_results_for_input(input_id: str, libraries: set[str]) -> list[ParserRunResult]:
    latest: dict[str, tuple[ParserRunResponse, ParserRunResult]] = {}
    for run_file in runs_root().glob("*/run.json"):
        try:
            run = ParserRunResponse.model_validate(_read_json(run_file, {}))
        except Exception:
            continue
        if run.input.id != input_id:
            continue
        for result in run.results:
            if result.library not in libraries or result.status != ParserStatus.OK:
                continue
            existing = latest.get(result.library)
            if existing is None or run.started_at > existing[0].started_at:
                latest[result.library] = (run, _rehydrate_result_text(result))
    return [latest[library][1] for library in sorted(latest)]


def _rehydrate_result_text(result: ParserRunResult) -> ParserRunResult:
    output_path = Path(result.artifact_paths.output_md) if result.artifact_paths.output_md else None
    if not output_path or not output_path.exists():
        return result
    return result.model_copy(update={"raw_text": output_path.read_text(encoding="utf-8", errors="replace")})


def get_cleaned_evidence(run_id: str, library: str) -> dict[str, Any] | None:
    run = get_run(run_id)
    if not run:
        return None
    result = next((item for item in run.results if item.library == library), None)
    if not result:
        return None

    structured_path = Path(result.artifact_paths.structured_json) if result.artifact_paths.structured_json else None
    if structured_path and structured_path.exists():
        payload = _read_json(structured_path, {})
        cleaned = payload.get("cleaned_evidence")
        if isinstance(cleaned, dict):
            return cleaned
    return clean_parser_result(result)


def save_corrections(run_id: str, library: str, payload: ParserCorrection) -> ParserCorrection | None:
    detail = get_result_detail(run_id, library)
    if not detail:
        return None
    corrections_path = Path(detail.result.artifact_paths.corrections_json) if detail.result.artifact_paths.corrections_json else None
    if not corrections_path:
        return None
    saved = payload.model_copy(update={"updated_at": utcnow()})
    _write_json(corrections_path, saved.model_dump(mode="json"))
    return saved


def score_ground_truth(text: str, ground_truth: ParserGroundTruth) -> list[ParserQualityCheck]:
    haystack = text.lower()
    checks: list[ParserQualityCheck] = []
    for term in ground_truth.expected_terms:
        expected = term.strip()
        if not expected:
            continue
        found = expected.lower() in haystack
        checks.append(
            ParserQualityCheck(
                key=safe_name(expected).lower(),
                label=expected,
                expected=expected,
                found=found,
                confidence=1.0 if found else 0.0,
                match_type="term",
            )
        )
    for field in ground_truth.expected_fields:
        expected = field.value.strip()
        if not expected:
            continue
        found = expected.lower() in haystack
        checks.append(
            ParserQualityCheck(
                key=field.key,
                label=field.label,
                expected=expected,
                found=found,
                confidence=1.0 if found else 0.0,
                match_type="field_value",
            )
        )
    return checks


def _structured_payload(result: ParserRunResult) -> dict[str, Any]:
    text = result.raw_text or result.text_preview or ""
    cleaned_evidence = clean_parser_result(result)
    pages = result.structured_preview.get("pages")
    blocks = result.structured_preview.get("blocks")
    return {
        "result": result.model_dump(mode="json"),
        "pages": pages
        if isinstance(pages, list) and pages
        else [
            {
                "page": 1,
                "text_preview": text[:4000],
                "chars": len(text),
            }
        ],
        "blocks": blocks
        if isinstance(blocks, list) and blocks
        else [
            {
                "id": f"{result.library}-block-1",
                "page": 1,
                "type": "text",
                "text_preview": text[:4000],
                "confidence": None,
            }
        ]
        if text
        else [],
        "tables": result.structured_preview.get("table_samples", []),
        "cleaned_evidence": cleaned_evidence,
        "raw_structured_preview": result.structured_preview,
    }


def _summary(run: ParserRunResponse) -> ParserRunSummary:
    ok_results = [result for result in run.results if result.status == ParserStatus.OK]
    fastest = min(ok_results, key=lambda result: result.seconds, default=None)
    return ParserRunSummary(
        run_id=run.run_id,
        input=run.input,
        parser_count=len(run.results),
        ok=sum(1 for result in run.results if result.status == ParserStatus.OK),
        skipped=sum(1 for result in run.results if result.status == ParserStatus.SKIPPED),
        failed=sum(1 for result in run.results if result.status == ParserStatus.FAILED),
        fastest_library=fastest.library if fastest else None,
        fastest_seconds=fastest.seconds if fastest else None,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _overall_confidence(checks: list[ParserQualityCheck], status: ParserStatus) -> float:
    if status != ParserStatus.OK:
        return 0.0
    if not checks:
        return 0.5
    return round(sum(check.confidence for check in checks) / len(checks), 3)
