"""PDF-Extract-Kit adapter using the official pdf2markdown project script."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from app.core.config import settings
from app.models.parser_benchmark import ParserRunResult
from app.services.parsers.base import (
    bbox_from_values,
    input_type_for,
    make_block,
    ok_result,
    project_root,
    skipped_result,
    structured_preview_from_blocks,
)

LIBRARY_ID = "pdf_extract_kit"
DISPLAY_NAME = "PDF-Extract-Kit"
SUPPORTED_INPUT_TYPES = ["pdf", "image"]


def is_available() -> bool:
    repo = _repo_root()
    return bool(
        repo
        and _project_script(repo).exists()
        and _python_available()
        and not _missing_model_paths(repo)
    )


def availability_notes() -> str | None:
    repo = _repo_root()
    if not repo:
        return (
            "Clone opendatalab/PDF-Extract-Kit and set EXTRACT_PDF_EXTRACT_KIT_REPO "
            "to that local checkout."
        )
    if not _project_script(repo).exists():
        return "PDF-Extract-Kit repo found, but project/pdf2markdown/scripts/run_project.py is missing."
    if not _python_available():
        return f"Python executable not found: {settings.pdf_extract_kit_python}"
    missing = _missing_model_paths(repo)
    if missing:
        return "Model weights missing: " + ", ".join(path.name for path in missing[:4])
    return None


def parse(input_path: Path, preview_chars: int = 1500) -> ParserRunResult:
    start = time.perf_counter()
    input_type = input_type_for(input_path)
    if input_type not in SUPPORTED_INPUT_TYPES:
        return skipped_result(
            LIBRARY_ID,
            input_path,
            "PDF-Extract-Kit supports PDF and image inputs in this adapter.",
            preview_chars=preview_chars,
        )

    repo = _repo_root()
    if not repo:
        return skipped_result(
            LIBRARY_ID,
            input_path,
            availability_notes() or "PDF-Extract-Kit is not configured.",
            preview_chars=preview_chars,
        )
    script = _project_script(repo)
    if not script.exists():
        return skipped_result(
            LIBRARY_ID,
            input_path,
            availability_notes() or "PDF-Extract-Kit project script is missing.",
            preview_chars=preview_chars,
        )
    missing = _missing_model_paths(repo)
    if missing:
        return skipped_result(
            LIBRARY_ID,
            input_path,
            (
                "PDF-Extract-Kit model weights are not ready. Download the official "
                "opendatalab/pdf-extract-kit-1.0 weights into the configured model root. "
                f"Missing: {', '.join(str(path) for path in missing)}"
            ),
            preview_chars=preview_chars,
        )

    work_dir = _work_root() / f"pek-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    output_dir = work_dir / "outputs"
    config_path = work_dir / "pdf2markdown.config.yaml"
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(_config(input_path, output_dir, repo), indent=2), encoding="utf-8")

    command = [
        settings.pdf_extract_kit_python,
        str(script),
        "--config",
        str(config_path),
    ]
    completed = subprocess.run(
        command,
        cwd=str(repo),
        text=True,
        capture_output=True,
        timeout=settings.pdf_extract_kit_timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(_subprocess_error(completed))

    markdown_files = sorted(output_dir.rglob("*.md"))
    json_files = sorted(output_dir.rglob("*.json"))
    structured_payloads = _read_json_files(json_files)
    text = _read_markdown(markdown_files) or _markdown_from_json(structured_payloads)
    if not text.strip():
        raise RuntimeError(f"PDF-Extract-Kit completed but no Markdown or JSON text was found in {output_dir}")

    stats = _stats_from_json(structured_payloads, fallback_pages=1 if input_type == "image" else 0)
    return ok_result(
        LIBRARY_ID,
        input_path,
        time.perf_counter() - start,
        text,
        pages=stats["pages"],
        tables=stats["tables"],
        images=stats["images"],
        structured_preview=structured_preview_from_blocks(
            stats["blocks"],
            text,
            preview_chars,
            {
            "adapter": "subprocess_pdf2markdown",
            "repo": str(repo),
            "config": str(config_path),
            "output_dir": str(output_dir),
            "markdown_files": [str(path) for path in markdown_files[:10]],
            "json_files": [str(path) for path in json_files[:10]],
            "block_samples": stats["block_samples"],
            "stdout_tail": _tail(completed.stdout),
            "stderr_tail": _tail(completed.stderr),
            },
        ),
        preview_chars=preview_chars,
    )


def _repo_root() -> Path | None:
    candidates: list[Path] = []
    if settings.pdf_extract_kit_repo:
        candidates.append(Path(settings.pdf_extract_kit_repo).expanduser())
    candidates.extend(
        [
            project_root() / "external" / "PDF-Extract-Kit",
            project_root() / "PDF-Extract-Kit",
        ]
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return None


def _project_script(repo: Path) -> Path:
    return repo / "project" / "pdf2markdown" / "scripts" / "run_project.py"


def _python_available() -> bool:
    executable = settings.pdf_extract_kit_python
    return Path(executable).exists() or shutil.which(executable) is not None


def _model_root(repo: Path) -> Path:
    if settings.pdf_extract_kit_model_root:
        return Path(settings.pdf_extract_kit_model_root).expanduser().resolve()
    return repo


def _model_paths(repo: Path) -> dict[str, Path]:
    model_root = _model_root(repo)
    return {
        "layout": model_root / "models" / "Layout" / "YOLO" / "doclayout_yolo_ft.pt",
        "formula_detection": model_root / "models" / "MFD" / "YOLO" / "yolo_v8_ft.pt",
        "formula_recognition": model_root / "models" / "MFR" / "unimernet_tiny",
        "ocr_det": model_root / "models" / "OCR" / "PaddleOCR" / "det" / "ch_PP-OCRv4_det",
        "ocr_rec": model_root / "models" / "OCR" / "PaddleOCR" / "rec" / "ch_PP-OCRv4_rec",
    }


def _missing_model_paths(repo: Path) -> list[Path]:
    return [path for path in _model_paths(repo).values() if not path.exists()]


def _work_root() -> Path:
    root = project_root() / "backend" / "parser_outputs" / "_pdf_extract_kit_work"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _config(input_path: Path, output_dir: Path, repo: Path) -> dict[str, Any]:
    models = _model_paths(repo)
    return {
        "inputs": str(input_path.resolve()),
        "outputs": str(output_dir.resolve()),
        "visualize": False,
        "merge2markdown": True,
        "tasks": {
            "layout_detection": {
                "model": "layout_detection_yolo",
                "model_config": {
                    "img_size": 1024,
                    "conf_thres": 0.25,
                    "iou_thres": 0.45,
                    "model_path": str(models["layout"]),
                },
            },
            "formula_detection": {
                "model": "formula_detection_yolo",
                "model_config": {
                    "img_size": 1280,
                    "conf_thres": 0.25,
                    "iou_thres": 0.45,
                    "batch_size": 1,
                    "model_path": str(models["formula_detection"]),
                },
            },
            "formula_recognition": {
                "model": "formula_recognition_unimernet",
                "model_config": {
                    "batch_size": 128,
                    "cfg_path": str(repo / "pdf_extract_kit" / "configs" / "unimernet.yaml"),
                    "model_path": str(models["formula_recognition"]),
                },
            },
            "ocr": {
                "model": "ocr_ppocr",
                "model_config": {
                    "lang": "ch",
                    "show_log": False,
                    "det_model_dir": str(models["ocr_det"]),
                    "rec_model_dir": str(models["ocr_rec"]),
                    "det_db_box_thresh": 0.3,
                },
            },
        },
    }


def _read_markdown(paths: list[Path]) -> str:
    return "\n\n".join(path.read_text(encoding="utf-8", errors="replace") for path in paths)


def _read_json_files(paths: list[Path]) -> list[Any]:
    payloads: list[Any] = []
    for path in paths:
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return payloads


def _page_payloads(payloads: Iterable[Any]) -> Iterable[dict[str, Any]]:
    for payload in payloads:
        if isinstance(payload, dict) and "layout_dets" in payload:
            yield payload
        elif isinstance(payload, dict):
            yield from _page_payloads(payload.values())
        elif isinstance(payload, list):
            yield from _page_payloads(payload)


def _markdown_from_json(payloads: list[Any]) -> str:
    pages: list[str] = []
    for page in _page_payloads(payloads):
        page_info = page.get("page_info", {}) if isinstance(page.get("page_info"), dict) else {}
        page_no = page_info.get("page_no", len(pages))
        blocks: list[str] = []
        for block in page.get("layout_dets", []) or []:
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            category = block.get("category_type") or "text"
            score = block.get("score")
            prefix = f"[{category}"
            if isinstance(score, (int, float)):
                prefix += f" score={score:.3f}"
            prefix += "]"
            blocks.append(f"{prefix} {text}")
        if blocks:
            pages.append(f"## Page {int(page_no) + 1}\n\n" + "\n\n".join(blocks))
    return "\n\n".join(pages)


def _stats_from_json(payloads: list[Any], fallback_pages: int = 0) -> dict[str, Any]:
    pages = list(_page_payloads(payloads))
    tables = 0
    images = 0
    blocks: list[dict[str, Any]] = []
    block_samples: list[dict[str, Any]] = []
    for page in pages:
        page_info = page.get("page_info", {}) if isinstance(page.get("page_info"), dict) else {}
        page_no = page_info.get("page_no", 0)
        for block in page.get("layout_dets", []) or []:
            if not isinstance(block, dict):
                continue
            category = str(block.get("category_type") or "").lower()
            if "table" in category:
                tables += 1
            if category in {"image", "figure"} or "figure" in category:
                images += 1
            parsed_block = make_block(
                LIBRARY_ID,
                int(page_no) + 1,
                category or "text",
                str(block.get("text") or ""),
                bbox=_bbox_from_block(block),
                provenance={
                    "source": "PDF-Extract-Kit layout_dets",
                    "category_type": block.get("category_type"),
                    "score": block.get("score"),
                },
                confidence=block.get("score") if isinstance(block.get("score"), (int, float)) else None,
            )
            if parsed_block:
                blocks.append(parsed_block)
            if len(block_samples) < 8:
                block_samples.append(
                    {
                        "page": int(page_no) + 1,
                        "type": category or "text",
                        "score": block.get("score"),
                        "bbox": _bbox_from_block(block),
                        "text_preview": str(block.get("text") or "")[:300],
                    }
                )
    return {
        "pages": len(pages) or fallback_pages,
        "tables": tables,
        "images": images,
        "blocks": blocks,
        "block_samples": block_samples,
    }


def _bbox_from_block(block: dict[str, Any]) -> dict[str, float] | None:
    for key in ("bbox", "poly", "box"):
        value = block.get(key)
        if isinstance(value, list) and len(value) >= 4:
            if all(isinstance(item, (int, float)) for item in value[:4]):
                return bbox_from_values(value[0], value[1], value[2], value[3])
            if all(isinstance(item, list) and len(item) >= 2 for item in value):
                xs = [item[0] for item in value]
                ys = [item[1] for item in value]
                return bbox_from_values(min(xs), min(ys), max(xs), max(ys))
    return None


def _subprocess_error(completed: subprocess.CompletedProcess[str]) -> str:
    return (
        f"PDF-Extract-Kit exited with code {completed.returncode}. "
        f"stdout: {_tail(completed.stdout)} stderr: {_tail(completed.stderr)}"
    )


def _tail(value: str, limit: int = 2000) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[-limit:]
