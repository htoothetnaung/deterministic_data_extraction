"""OCR extraction service.

PLACEHOLDER. Implement real OCR here.

Responsibilities:
  * Run an OCR engine over a parsed document.
  * Produce structural blocks (text, headings, tables, key-value pairs).
  * Attach per-block confidence scores.

Suggested engines:
  * ``pytesseract`` (Tesseract wrapper)
  * ``paddleocr``
  * ``easyocr``
  * Cloud / VLM-based OCR (e.g. GLM-4.6V via z-ai-web-dev-sdk)

TODO: replace ``run_ocr`` with a real implementation.
"""
from __future__ import annotations

from typing import Any


def run_ocr(document_id: str, parsed: dict[str, Any]) -> dict[str, Any]:
    """Run OCR over a parsed document and return blocks + confidence.

    Returns a dict with:
        { "engine": str, "pages": int, "blocks": list[dict], "overall_confidence": float }
    """
    # TODO: implement real OCR.
    return {
        "engine": "placeholder-ocr",
        "pages": parsed.get("page_count", 1),
        "blocks": [],
        "overall_confidence": 0.0,
    }
