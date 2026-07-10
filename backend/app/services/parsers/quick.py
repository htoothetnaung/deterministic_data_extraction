"""Lightweight document metadata parsing for ingestion and routing."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


def parse_document(file_path: str | Path) -> dict[str, Any]:
    """Return cheap structural metadata without running the full parser stack."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    text_pages: list[str] = [""]
    page_count = 1

    if suffix == ".pdf":
        page_count, text_pages = _parse_pdf_quick(path)
    elif suffix == ".docx":
        text = _docx_text(path)
        text_pages = [text]
    elif suffix in {".txt", ".md", ".csv", ".tsv", ".json"}:
        text_pages = [path.read_text(encoding="utf-8", errors="replace")[:4000]]

    first_page = text_pages[0] if text_pages else ""
    return {
        "page_count": max(page_count, 1),
        "text_pages": text_pages,
        "first_page_text": first_page[:4000],
        "document_type": detect_document_type_from_text(path.name, first_page),
        "meta": {
            "name": path.name,
            "size_bytes": path.stat().st_size if path.exists() else 0,
            "suffix": suffix,
        },
    }


def detect_document_type(file_path: str | Path) -> str:
    return detect_document_type_from_text(Path(file_path).name, "")


def detect_document_type_from_text(filename: str, text: str) -> str:
    haystack = f"{filename}\n{text}".lower()
    patterns = [
        ("annual_report", r"annual report|directors[’'] statement|financial statements"),
        ("financial_statement", r"income statement|balance sheet|cash flow|statement of financial position"),
        ("invoice", r"\binvoice\b|invoice no|amount due"),
        ("receipt", r"\breceipt\b|paid on|payment received"),
        ("contract", r"\bagreement\b|\bcontract\b|terms and conditions"),
        ("proxy_form", r"proxy form|appoint.*proxy|general meeting"),
        ("bank_statement", r"bank statement|account statement|opening balance|closing balance"),
    ]
    for label, pattern in patterns:
        if re.search(pattern, haystack, re.I):
            return label
    return "other"


def _parse_pdf_quick(path: Path) -> tuple[int, list[str]]:
    try:
        import fitz
    except ImportError:
        return 1, [""]

    with fitz.open(str(path)) as document:
        page_count = document.page_count
        pages: list[str] = []
        for index in range(min(page_count, 3)):
            try:
                pages.append(document.load_page(index).get_text("text")[:4000])
            except Exception:
                pages.append("")
    return page_count, pages or [""]


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except Exception:
        return ""
    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [text.text or "" for text in paragraph.findall(".//w:t", namespace) if text.text]
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs)
