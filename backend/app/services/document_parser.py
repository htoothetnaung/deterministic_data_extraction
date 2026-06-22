"""Document parser service.

This module is a PLACEHOLDER. Implement the real document parsing logic here.

Responsibilities (to implement):
  * Accept an uploaded file path (PDF, image, DOCX, ...).
  * Extract page count, text layer, images, and structural metadata.
  * Persist parsed artefacts for downstream OCR / chunking.

Suggested libraries:
  * PDF: ``pypdf`` / ``pdfplumber`` / ``PyMuPDF (fitz)``
  * DOCX: ``python-docx``
  * Images: ``Pillow``
  * Layout analysis: ``layoutparser`` or a custom detector.

TODO: replace ``parse_document`` with a real implementation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def parse_document(file_path: str | Path) -> dict[str, Any]:
    """Parse a document and return structural metadata.

    Returns a dict with at least:
        { "page_count": int, "text_pages": list[str], "meta": dict }
    """
    # TODO: implement real parsing.
    path = Path(file_path)
    return {
        "page_count": 1,
        "text_pages": [""],
        "meta": {
            "name": path.name,
            "size_bytes": path.stat().st_size if path.exists() else 0,
        },
    }


def detect_document_type(file_path: str | Path) -> str:
    """Heuristically detect the document type (invoice, contract, ...)."""
    # TODO: implement type detection (rules or a small classifier).
    return "other"
