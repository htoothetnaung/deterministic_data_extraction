"""Low-latency parser-output cleanup for extraction evidence.

This layer is intentionally deterministic for now. It normalizes parser blocks,
markdown tables, pages, and image references into evidence records that are more
stable than raw parser markdown but cheaper than an LLM/VLM repair pass.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
import logging
logger = logging.getLogger(__name__)

from app.models.parser_benchmark import ParserRunResult, ParserStatus
from app.services.parsers.base import preview_text

SUPPORTED_CLEANUP_PARSERS = {"layout_pdfplumber", "mistral_ocr", "paddleocr_vl_vllm", "docling"}

FINANCIAL_TERMS = {
    "asset",
    "assets",
    "liabilities",
    "liability",
    "equity",
    "revenue",
    "profit",
    "loss",
    "income",
    "cash",
    "deposit",
    "borrowings",
    "payables",
    "receivables",
    "amortised",
    "fair value",
    "financial",
    "statement",
    "$'000",
    "$000",
}


@dataclass
class CleanEvidence:
    id: str
    parser_id: str
    page: int
    type: str
    text: str
    bbox: dict[str, float] | None
    confidence: float
    risk: str
    warnings: list[str]
    provenance: dict[str, Any]
    columns: list[str] | None = None
    rows: list[dict[str, str]] | None = None

    def to_dict(self, include_text: bool = True) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "parser_id": self.parser_id,
            "page": self.page,
            "type": self.type,
            "text_preview": preview_text(self.text, 1600),
            "bbox": self.bbox,
            "confidence": round(self.confidence, 3),
            "risk": self.risk,
            "warnings": self.warnings,
            "provenance": self.provenance,
        }
        if include_text:
            payload["text"] = self.text
        if self.columns is not None:
            payload["columns"] = self.columns
        if self.rows is not None:
            payload["rows"] = self.rows
            payload["row_count"] = len(self.rows)
        return payload


def clean_parser_result(result: ParserRunResult, max_pages: int | None = None) -> dict[str, Any]:
    """Return normalized evidence for parsers currently approved for cleanup."""
    parser_id = result.library
    if parser_id not in SUPPORTED_CLEANUP_PARSERS or result.status != ParserStatus.OK:
        logger.info("evidence_cleaner: disabled parser=%s reason=unsupported_or_failed", parser_id)
        return {
            "enabled": False,
            "parser_id": parser_id,
            "reason": "cleanup is currently enabled only for supported Extraction Lab parser OK results",
            "items": [],
            "stats": {"items": 0, "tables": 0, "text_blocks": 0, "images": 0},
        }

    items: list[CleanEvidence] = []
    blocks = result.structured_preview.get("blocks")
    if isinstance(blocks, list):
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            item = _clean_block(parser_id, block, index)
            if item and (max_pages is None or item.page <= max_pages):
                items.append(item)

    logger.info("evidence_cleaner: parsed_blocks blocks=%d items=%d", len(blocks) if isinstance(blocks, list) else 0, len(items))

    raw_text = result.raw_text or result.text_preview
    # OCR/VLM parsers often produce one large markdown block per page. Recover
    # tables and image references from markdown/html when structured metadata is
    # sparse.
    tables_from_raw = _tables_from_raw_text(parser_id, raw_text)
    images_from_raw = _images_from_raw_text(parser_id, raw_text)
    logger.info("evidence_cleaner: recovered_raw tables=%d images=%d", len(tables_from_raw), len(images_from_raw))
    for item in [*tables_from_raw, *images_from_raw]:
        if max_pages is None or item.page <= max_pages:
            items.append(item)

    items = _dedupe_items(items)
    logger.info("evidence_cleaner: after_dedup items=%d", len(items))
    pages = sorted({item.page for item in items})
    stats = {
        "items": len(items),
        "tables": sum(1 for item in items if item.type == "table"),
        "text_blocks": sum(1 for item in items if item.type in {"text", "heading", "title", "list", "markdown", "page"}),
        "images": sum(1 for item in items if item.type == "image"),
        "financial_risk_items": sum(1 for item in items if item.risk == "financial_review"),
        "pages": pages[:200],
    }
    logger.info("evidence_cleaner: enabled parser=%s items=%d tables=%d text_blocks=%d images=%d", parser_id, stats['items'], stats['tables'], stats['text_blocks'], stats['images'])
    return {
        "enabled": True,
        "parser_id": parser_id,
        "strategy": "deterministic_evidence_cleanup_v1",
        "items": [item.to_dict(include_text=True) for item in items],
        "stats": stats,
        "note": "Deterministic cleanup: no LLM/VLM calls. Parser markdown, HTML tables, and image references are normalized; financial tables are lowered in confidence and flagged for manual review.",
    }


def cleaned_items_for_extraction(result: ParserRunResult, max_pages: int) -> list[dict[str, Any]]:
    payload = clean_parser_result(result, max_pages=max_pages)
    if not payload.get("enabled"):
        return []
    items = payload.get("items")
    return items if isinstance(items, list) else []


def _clean_block(parser_id: str, block: dict[str, Any], index: int) -> CleanEvidence | None:
    text = str(block.get("text") or block.get("text_preview") or "").strip()
    block_type = str(block.get("type") or "text").lower()
    if not text and block_type != "image":
        return None
    page = _safe_int(block.get("page"), 1)
    bbox = block.get("bbox") if isinstance(block.get("bbox"), dict) else None
    provenance = block.get("provenance") if isinstance(block.get("provenance"), dict) else {}
    confidence = _safe_float(block.get("confidence"), _base_confidence(parser_id, block_type, text))
    warnings: list[str] = []
    columns = None
    rows = None

    if block_type == "image":
        text = _image_text_from_markdown(text) or text or "Image evidence"

    if block_type == "table" or _contains_markdown_table(text) or _contains_html_table(text):
        table = _best_markdown_table(text)
        if not table:
            table = _best_html_table(text)
        if table:
            columns, rows, text = table
            block_type = "table"
        else:
            warnings.append("table_structure_not_recovered")
            block_type = "table"

    risk = _risk_for_item(block_type, text)
    if risk == "financial_review":
        confidence = min(confidence, 0.58)
        warnings.append("financial_content_requires_manual_review")

    return CleanEvidence(
        id=_evidence_id(parser_id, page, block_type, index, text, bbox),
        parser_id=parser_id,
        page=page,
        type=block_type,
        text=_clean_text(text),
        bbox=bbox,
        confidence=confidence,
        risk=risk,
        warnings=warnings,
        provenance={
            "source": "evidence_cleaner.block",
            "original_type": block.get("type"),
            "parser_provenance": provenance,
        },
        columns=columns,
        rows=rows,
    )


def _tables_from_raw_text(parser_id: str, text: str) -> list[CleanEvidence]:
    if not text:
        return []
    output: list[CleanEvidence] = []
    current_page = 1
    for segment in _page_segments(text):
        current_page = segment["page"]
        for index, table_text in enumerate(_markdown_table_groups(segment["text"])):
            parsed = _parse_markdown_table(table_text)
            if not parsed:
                continue
            columns, rows, normalized = parsed
            risk = _risk_for_item("table", normalized)
            confidence = 0.72
            warnings: list[str] = ["recovered_from_page_markdown"]
            if risk == "financial_review":
                confidence = 0.52
                warnings.append("financial_content_requires_manual_review")
            output.append(
                CleanEvidence(
                    id=_evidence_id(parser_id, current_page, "table", index, normalized, None),
                    parser_id=parser_id,
                    page=current_page,
                    type="table",
                    text=normalized,
                    bbox=None,
                    confidence=confidence,
                    risk=risk,
                    warnings=warnings,
                    provenance={"source": "evidence_cleaner.raw_markdown_table"},
                    columns=columns,
                    rows=rows,
                )
            )
        for index, parsed in enumerate(_html_tables(segment["text"])):
            columns, rows, normalized = parsed
            risk = _risk_for_item("table", normalized)
            confidence = 0.76
            warnings: list[str] = ["recovered_from_html_table"]
            if risk == "financial_review":
                confidence = 0.54
                warnings.append("financial_content_requires_manual_review")
            output.append(
                CleanEvidence(
                    id=_evidence_id(parser_id, current_page, "table", index, normalized, None),
                    parser_id=parser_id,
                    page=current_page,
                    type="table",
                    text=normalized,
                    bbox=None,
                    confidence=confidence,
                    risk=risk,
                    warnings=warnings,
                    provenance={"source": "evidence_cleaner.raw_html_table"},
                    columns=columns,
                    rows=rows,
                )
            )
    return output


def _images_from_raw_text(parser_id: str, text: str) -> list[CleanEvidence]:
    if not text:
        return []
    output: list[CleanEvidence] = []
    for segment in _page_segments(text):
        for index, match in enumerate(re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", segment["text"])):
            label = (match.group(1) or "Image evidence").strip()
            url = match.group(2).strip()
            evidence_text = f"{label}\n{url}"
            output.append(
                CleanEvidence(
                    id=_evidence_id(parser_id, segment["page"], "image", index, evidence_text, None),
                    parser_id=parser_id,
                    page=segment["page"],
                    type="image",
                    text=evidence_text,
                    bbox=None,
                    confidence=0.62,
                    risk="normal",
                    warnings=[],
                    provenance={"source": "evidence_cleaner.raw_markdown_image", "url": url},
                )
            )
    return output


def _page_segments(text: str) -> list[dict[str, Any]]:
    markers = list(re.finditer(r"<!--\s*page:\s*(\d+)\s*-->", text, re.I))
    if not markers:
        return [{"page": 1, "text": text}]
    segments: list[dict[str, Any]] = []
    for index, marker in enumerate(markers):
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        segments.append({"page": int(marker.group(1)), "text": text[start:end]})
    return segments


def _contains_markdown_table(text: str) -> bool:
    return any(line.strip().startswith("|") and line.strip().endswith("|") for line in text.splitlines())


def _contains_html_table(text: str) -> bool:
    return "<table" in text.lower() and "</table" in text.lower()


def _best_markdown_table(text: str) -> tuple[list[str], list[dict[str, str]], str] | None:
    tables = _markdown_table_groups(text)
    parsed = [_parse_markdown_table(table) for table in tables]
    parsed = [table for table in parsed if table]
    if not parsed:
        return None
    return max(parsed, key=lambda table: len(table[1]))


def _best_html_table(text: str) -> tuple[list[str], list[dict[str, str]], str] | None:
    parsed = _html_tables(text)
    if not parsed:
        return None
    return max(parsed, key=lambda table: len(table[1]))


def _html_tables(text: str) -> list[tuple[list[str], list[dict[str, str]], str]]:
    if not _contains_html_table(text):
        return []
    parser = _TableHTMLParser()
    try:
        parser.feed(text)
    except Exception as e:
        logger.debug("evidence_cleaner: html_table_parse failed: %s", e)
        return []
    output: list[tuple[list[str], list[dict[str, str]], str]] = []
    for table in parser.tables:
        if len(table) < 2:
            continue
        width = max(len(row) for row in table)
        rows = [row + [""] * (width - len(row)) for row in table]
        headers = [_clean_header(cell, index) for index, cell in enumerate(rows[0])]
        records = [
            {headers[index]: _clean_cell(row[index]) for index in range(width)}
            for row in rows[1:]
            if any(cell.strip() for cell in row)
        ]
        if records:
            output.append((headers, records, _records_to_markdown(headers, records)))
    return output


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_cell = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and tag == "tr":
            self._current_row = []
        elif self._in_table and tag in {"td", "th"}:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            self._current_row.append(_clean_cell(" ".join(self._current_cell)))
            self._current_cell = []
            self._in_cell = False
        elif tag == "tr" and self._in_table:
            if any(cell.strip() for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = []
        elif tag == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = []
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def _markdown_table_groups(text: str) -> list[str]:
    groups: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            current.append(stripped)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return ["\n".join(group) for group in groups]


def _parse_markdown_table(text: str) -> tuple[list[str], list[dict[str, str]], str] | None:
    raw_rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in text.splitlines()
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]
    rows = [
        row
        for row in raw_rows
        if row and not all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in row)
    ]
    if len(rows) < 2:
        logger.debug("evidence_cleaner: markdown_table_parse rows=%d reason=too_few_rows", len(rows))
        return None
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    headers = [_clean_header(cell, index) for index, cell in enumerate(rows[0])]
    records = [
        {headers[index]: _clean_cell(row[index]) for index in range(width)}
        for row in rows[1:]
        if any(cell.strip() for cell in row)
    ]
    if not records:
        return None
    normalized = _records_to_markdown(headers, records)
    return headers, records, normalized


def _records_to_markdown(headers: list[str], records: list[dict[str, str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for record in records:
        lines.append("| " + " | ".join(record.get(header, "") for header in headers) + " |")
    return "\n".join(lines)


def _clean_header(value: str, index: int) -> str:
    clean = re.sub(r"\s+", " ", value.replace("\n", " ")).strip()
    return clean or f"column_{index + 1}"


def _clean_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\n", " ")).strip()


def _clean_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _image_text_from_markdown(value: str) -> str:
    match = re.search(r"!\[([^\]]*)\]\(([^)]+)\)", value)
    if not match:
        return ""
    label = (match.group(1) or "Image evidence").strip()
    return f"{label}\n{match.group(2).strip()}"


def _risk_for_item(item_type: str, text: str) -> str:
    normalized = text.lower()
    if item_type == "table" and any(term in normalized for term in FINANCIAL_TERMS):
        return "financial_review"
    return "normal"


def _base_confidence(parser_id: str, block_type: str, text: str) -> float:
    if block_type == "table":
        return 0.82 if parser_id == "layout_pdfplumber" else 0.74
    if block_type in {"title", "heading"}:
        return 0.86
    if block_type == "image":
        return 0.6
    return 0.78 if len(text) > 20 else 0.62


def _dedupe_items(items: list[CleanEvidence]) -> list[CleanEvidence]:
    seen: set[tuple[int, str, str]] = set()
    output: list[CleanEvidence] = []
    for item in items:
        key = (item.page, item.type, hashlib.sha1(item.text[:2000].encode("utf-8")).hexdigest()[:12])
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return sorted(output, key=lambda item: (item.page, _type_order(item.type), item.id))


def _type_order(item_type: str) -> int:
    return {"title": 0, "heading": 1, "table": 2, "text": 3, "markdown": 4, "image": 5}.get(item_type, 9)


def _evidence_id(parser_id: str, page: int, item_type: str, index: int, text: str, bbox: dict[str, Any] | None) -> str:
    digest = hashlib.sha1(f"{parser_id}|{page}|{item_type}|{index}|{text[:240]}|{bbox}".encode("utf-8")).hexdigest()[:14]
    return f"cev-{parser_id}-p{page}-{item_type}-{digest}"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(parsed, 1.0))
