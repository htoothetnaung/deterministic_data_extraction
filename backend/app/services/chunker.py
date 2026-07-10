"""Multi-granularity chunker for document parser output.

Decomposes a ParserRunResult into retrieval-ready Chunk objects at a chosen granularity:
* 'document'      -> One chunk for the entire document (coarsest).
* 'page'          -> One chunk per page.
* 'table_row'     -> Decomposes tables into self-describing row chunks (with headers repeated).
* 'sliding_window'-> Token-aware overlapping sliding windows.
* 'block'         -> One chunk per parser-identified block.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser as __HTML_PARSER_BASE
from typing import Any, Literal, Optional

from app.models.parser_benchmark import ParserRunResult, ParserStatus
from app.services.parsers.base import preview_text

ChunkStrategy = Literal["document", "page", "table_row", "sliding_window", "block"]

DEFAULT_STRATEGY: ChunkStrategy = "table_row"

_PAGE_MARKER = re.compile(r"<!--\s*page:\s*(\d+)\s*-->", re.IGNORECASE)


@dataclass
class ChunkConfig:
    """Configuration limits and thresholds for the chunking logic.

    Includes settings for maximum page processing boundaries, target sliding window sizes,
    overlaps, and table rows.
    """
    max_pages: int = 50
    chunk_size: int = 500
    chunk_overlap: int = 80
    min_chunk_chars: int = 1
    max_table_rows: int = 500


@dataclass
class Chunk:
    """Represents a discrete text or table segment parsed from a document.

    Holds granular indexing properties such as page context, bounding boxes, row/column lists,
    estimated tiktoken tokens, and stable cryptographic IDs for RAG retrieval matching.
    """
    chunk_id: str
    page: int
    chunk_type: str
    text: str
    bbox: Optional[dict[str, float]] = None
    confidence: Optional[float] = None
    risk: str = "normal"
    warnings: list[str] = field(default_factory=list)
    source_url: Optional[str] = None
    columns: Optional[list[str]] = None
    rows: Optional[list[dict[str, str]]] = None
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    header: Optional[list[str]] = None
    token_count: Optional[int] = None
    strategy: str = DEFAULT_STRATEGY
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        """Return the number of characters in the chunk text."""
        return len(self.text)

    @property
    def text_preview(self) -> str:
        """Return a shortened preview snippet of the chunk text."""
        return preview_text(self.text, 1600)

    def to_dict(self, include_text: bool = True) -> dict[str, Any]:
        """Convert the Chunk dataclass into a serialized dictionary for storage or JSON APIs."""
        payload: dict[str, Any] = {
            "id": self.chunk_id,
            "page": self.page,
            "type": self.chunk_type,
            "text_preview": self.text_preview,
            "bbox": self.bbox,
            "confidence": round(self.confidence, 3) if self.confidence is not None else None,
            "risk": self.risk,
            "warnings": self.warnings,
            "source_url": self.source_url,
            "strategy": self.strategy,
            "char_count": self.char_count,
            "token_count": self.token_count,
        }
        if include_text:
            payload["text"] = self.text
        if self.columns is not None:
            payload["columns"] = self.columns
        if self.rows is not None:
            payload["rows"] = self.rows
            payload["row_count"] = len(self.rows)
        if self.table_index is not None:
            payload["table_index"] = self.table_index
        if self.row_index is not None:
            payload["row_index"] = self.row_index
        if self.header is not None:
            payload["header"] = self.header
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


def chunk_parser_result(
    result: ParserRunResult,
    strategy: ChunkStrategy = DEFAULT_STRATEGY,
    config: Optional[ChunkConfig] = None,
) -> list[Chunk]:
    """Decompose a parsed document into indexing units using the specified strategy.

    Routes execution to helper strategies ('document', 'page', 'table_row', 'sliding_window', 'block')
    and runs a final deduplication pass to prevent duplicate indexing.
    """
    cfg = config or ChunkConfig()
    if strategy == "document":
        chunks = _chunk_document(result, cfg)
    elif strategy == "page":
        chunks = _chunk_pages(result, cfg)
    elif strategy == "table_row":
        chunks = _chunk_table_rows(result, cfg)
    elif strategy == "sliding_window":
        chunks = _chunk_sliding_window(result, cfg)
    else:
        chunks = _chunk_blocks(result, cfg)
    return _dedupe_chunks(chunks)


def _chunk_blocks(result: ParserRunResult, cfg: ChunkConfig) -> list[Chunk]:
    """Index each parser-extracted block (text paragraphs, figures, tables) as its own chunk."""
    items = _parser_items(result, cfg.max_pages)
    chunks: list[Chunk] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("text_preview") or "").strip()
        if len(text) < cfg.min_chunk_chars:
            continue
        page = _safe_int(item.get("page"), 1)
        if page > cfg.max_pages:
            continue
        chunks.append(
            Chunk(
                chunk_id=_stable_id("block", page, item.get("type", "text"), text, item.get("id")),
                page=page,
                chunk_type=str(item.get("type") or "text"),
                text=text,
                bbox=item.get("bbox") if isinstance(item.get("bbox"), dict) else None,
                confidence=_safe_float(item.get("confidence")),
                risk="normal",
                warnings=[],
                source_url=_source_url(item),
                columns=[str(c) for c in item.get("columns", [])] if isinstance(item.get("columns"), list) else None,
                rows=_coerce_rows(item.get("rows")),
                strategy="block",
                token_count=_estimate_tokens(text),
                metadata={"source": "parser_block"},
            )
        )
    return chunks


def _chunk_document(result: ParserRunResult, cfg: ChunkConfig) -> list[Chunk]:
    """Join the entire document's text transcript into a single large chunk."""
    text = _full_document_text(result)
    text = text.strip()
    if len(text) < cfg.min_chunk_chars:
        return []
    return [
        Chunk(
            chunk_id=_stable_id("document", 1, "document", text),
            page=1,
            chunk_type="document",
            text=text,
            confidence=None,
            strategy="document",
            token_count=_estimate_tokens(text),
            metadata={"source": "full_document", "pages": max(result.pages, 1)},
        )
    ]


def _chunk_pages(result: ParserRunResult, cfg: ChunkConfig) -> list[Chunk]:
    """Slice the document transcript by page boundaries, creating one chunk per page."""
    segments = _page_segments(result)
    chunks: list[Chunk] = []
    for page_number, page_text in segments:
        if page_number > cfg.max_pages:
            continue
        text = page_text.strip()
        if len(text) < cfg.min_chunk_chars:
            continue
        chunks.append(
            Chunk(
                chunk_id=_stable_id("page", page_number, "page", text),
                page=page_number,
                chunk_type="page",
                text=text,
                strategy="page",
                token_count=_estimate_tokens(text),
                metadata={"source": "page_text"},
            )
        )
    if chunks:
        return chunks
    return _chunk_document(result, cfg)


def _chunk_table_rows(result: ParserRunResult, cfg: ChunkConfig) -> list[Chunk]:
    """Decompose parsed tables into self-describing row units, while leaving text blocks intact.

    For each row in a table, repeats the columns headers inside a small markdown table chunk.
    This guarantees that table cell values remain contextually associated with their column meanings
    even when indexed in isolation for dense/sparse RAG.
    """
    items = _parser_items(result, cfg.max_pages)
    chunks: list[Chunk] = []
    table_counter = 0
    emitted_rows = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        page = _safe_int(item.get("page"), 1)
        if page > cfg.max_pages:
            continue
        text = str(item.get("text") or item.get("text_preview") or "").strip()
        item_type = str(item.get("type") or "text")
        columns = [str(c) for c in item.get("columns", [])] if isinstance(item.get("columns"), list) else None
        rows = _coerce_rows(item.get("rows"))

        is_table = item_type == "table" or columns or rows
        if is_table and rows and columns and len(columns) > 0:
            table_counter += 1
            bbox = item.get("bbox") if isinstance(item.get("bbox"), dict) else None
            row_bboxes = item.get("row_bboxes") if isinstance(item.get("row_bboxes"), list) else None
            header = columns
            for row_index, row in enumerate(rows):
                if emitted_rows >= cfg.max_table_rows:
                    break
                row_text = _row_to_self_describing_markdown(header, row)
                if not row_text.strip():
                    continue
                row_bbox = bbox
                if row_bboxes and row_index < len(row_bboxes) and isinstance(row_bboxes[row_index], dict):
                    row_bbox = row_bboxes[row_index]
                elif bbox and len(rows) > 0:
                    try:
                        top_val = float(bbox.get("top") if "top" in bbox else bbox.get("y0") or 0.0)
                        bot_val = float(bbox.get("bottom") if "bottom" in bbox else bbox.get("y1") or 0.0)
                        left_val = float(bbox.get("x0") if "x0" in bbox else bbox.get("left") or 0.0)
                        right_val = float(bbox.get("x1") if "x1" in bbox else bbox.get("right") or 0.0)
                        if bot_val > top_val:
                            row_h = (bot_val - top_val) / len(rows)
                            row_bbox = {
                                "x0": round(left_val, 2),
                                "top": round(top_val + row_index * row_h, 2),
                                "x1": round(right_val, 2),
                                "bottom": round(top_val + (row_index + 1) * row_h, 2),
                            }
                    except (TypeError, ValueError):
                        pass
                chunks.append(
                    Chunk(
                        chunk_id=_stable_id(
                            "table_row", page, "table_row",
                            f"{table_counter}:{row_index}:{row_text}",
                        ),
                        page=page,
                        chunk_type="table_row",
                        text=row_text,
                        bbox=row_bbox,
                        confidence=_safe_float(item.get("confidence")),
                        risk="normal",
                        warnings=[],
                        columns=header,
                        rows=[row],
                        table_index=table_counter,
                        row_index=row_index,
                        header=header,
                        strategy="table_row",
                        token_count=_estimate_tokens(row_text),
                        metadata={
                            "source": "parser_table_row",
                            "row_count_total": len(rows),
                        },
                    )
                )
                emitted_rows += 1
            continue

        if len(text) < cfg.min_chunk_chars:
            continue
        chunks.append(
            Chunk(
                chunk_id=_stable_id("block", page, item_type, text, item.get("id")),
                page=page,
                chunk_type=item_type,
                text=text,
                bbox=item.get("bbox") if isinstance(item.get("bbox"), dict) else None,
                confidence=_safe_float(item.get("confidence")),
                risk="normal",
                warnings=[],
                source_url=_source_url(item),
                columns=columns,
                rows=rows,
                strategy="table_row",
                token_count=_estimate_tokens(text),
                metadata={"source": "parser_block"},
            )
        )
    return chunks


def _chunk_sliding_window(result: ParserRunResult, cfg: ChunkConfig) -> list[Chunk]:
    """Construct overlapping token windows from the document text.

    Runs tiktoken tokenization over the text transcript of each page, constructing sliding
    windows defined by `chunk_size` and `chunk_overlap`.
    """
    segments = _page_segments(result)
    if not segments:
        text = _full_document_text(result)
        if text.strip():
            segments = [(1, text)]
        else:
            return []
    size = max(int(cfg.chunk_size), 1)
    overlap = max(0, min(int(cfg.chunk_overlap), size - 1))
    step = max(size - overlap, 1)

    chunks: list[Chunk] = []
    global_index = 0
    for page_number, page_text in segments:
        if page_number > cfg.max_pages:
            continue
        tokens = _tokenize(page_text)
        if not tokens:
            continue
        for start in range(0, len(tokens), step):
            window = tokens[start : start + size]
            if not window:
                break
            window_text = _detokenize(window)
            if len(window_text.strip()) < cfg.min_chunk_chars:
                if start + size >= len(tokens):
                    break
                continue
            chunks.append(
                Chunk(
                    chunk_id=_stable_id(
                        "sliding_window", page_number, "sliding_window",
                        f"{global_index}:{window_text}",
                    ),
                    page=page_number,
                    chunk_type="sliding_window",
                    text=window_text,
                    strategy="sliding_window",
                    token_count=len(window),
                    metadata={
                        "source": "sliding_window",
                        "window_index": global_index,
                        "window_start": start,
                        "window_size": size,
                        "window_overlap": overlap,
                    },
                )
                # Note: window_index is local to the page loop but global_index is incremented globally
            )
            global_index += 1
            if start + size >= len(tokens):
                break
    return chunks or _chunk_document(result, cfg)


def _parser_items(result: ParserRunResult, max_pages: int) -> list[dict[str, Any]]:
    """Retrieve blocks or tables from parsed markdown/HTML formats inside ParserRunResult.

    Collects block items from `structured_preview.blocks` and recovers additional tabular data
    from raw text strings.
    """
    if result.status != ParserStatus.OK:
        return []

    items: list[dict[str, Any]] = []
    blocks = result.structured_preview.get("blocks")
    if isinstance(blocks, list):
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            page = _safe_int(block.get("page"), 1)
            if page > max_pages:
                continue
            block_type = str(block.get("type") or "text").lower()
            text = str(block.get("text") or block.get("text_preview") or "").strip()
            columns = block.get("columns") if isinstance(block.get("columns"), list) else None
            rows = _coerce_rows(block.get("rows"))

            # Normalize table representation if rows/columns are found.
            if block_type == "table" or columns or rows:
                if columns and rows:
                    text = _row_table_to_markdown(columns, rows) or text
                elif text:
                    parsed = _best_table_in_text(text)
                    if parsed is not None:
                        columns, rows, text = parsed
                block_type = "table"

            if not text and block_type != "image":
                continue

            items.append(
                {
                    "id": f"blk-{index}",
                    "type": block_type,
                    "page": page,
                    "text": text,
                    "bbox": block.get("bbox") if isinstance(block.get("bbox"), dict) else None,
                    "confidence": block.get("confidence"),
                    "columns": columns,
                    "rows": rows,
                    "provenance": block.get("provenance") if isinstance(block.get("provenance"), dict) else {},
                }
            )

    raw_text = result.raw_text or ""
    for table in _tables_from_raw_text(raw_text):
        if table["page"] <= max_pages:
            items.append(table)

    return items


def _tables_from_raw_text(text: str) -> list[dict[str, Any]]:
    """Recover markdown and HTML tables from page-segmented raw text strings."""
    if not text:
        return []
    output: list[dict[str, Any]] = []
    for segment in _raw_page_segments(text):
        page = segment["page"]
        for index, parsed in enumerate(_markdown_tables_in(segment["text"])):
            columns, rows, normalized = parsed
            output.append(
                {
                    "id": f"mdtbl-{page}-{index}",
                    "type": "table",
                    "page": page,
                    "text": normalized,
                    "bbox": None,
                    "confidence": None,
                    "columns": columns,
                    "rows": rows,
                    "provenance": {"source": "parser_raw_markdown_table"},
                }
            )
        for index, parsed in enumerate(_html_tables_in(segment["text"])):
            columns, rows, normalized = parsed
            output.append(
                {
                    "id": f"htmltbl-{page}-{index}",
                    "type": "table",
                    "page": page,
                    "text": normalized,
                    "bbox": None,
                    "confidence": None,
                    "columns": columns,
                    "rows": rows,
                    "provenance": {"source": "parser_raw_html_table"},
                }
            )
    return output


def _raw_page_segments(text: str) -> list[dict[str, Any]]:
    """Split text into raw segments by page markers without stripping comments."""
    markers = list(_PAGE_MARKER.finditer(text)) if text else []
    if not markers:
        return [{"page": 1, "text": text}]
    segments: list[dict[str, Any]] = []
    for index, marker in enumerate(markers):
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        segments.append({"page": int(marker.group(1)), "text": text[start:end]})
    return segments


def _markdown_tables_in(text: str) -> list[tuple[list[str], list[dict[str, str]], str]]:
    """Detect and parse markdown pipe-tables in text block."""
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
    parsed = [_parse_markdown_table("\n".join(group)) for group in groups]
    return [table for table in parsed if table]


def _parse_markdown_table(text: str) -> tuple[list[str], list[dict[str, str]], str] | None:
    """Parse a single markdown pipe table block into columns, row dicts, and normalized markdown text."""
    raw_rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in text.splitlines()
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]
    rows = [row for row in raw_rows if row and not all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in row)]
    if len(rows) < 2:
        return None
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    headers = [str(rows[0][i] or f"column_{i + 1}") for i in range(width)]
    records = [
        {headers[i]: str(row[i]) for i in range(width)}
        for row in rows[1:]
        if any(cell.strip() for cell in row)
    ]
    if not records:
        return None
    return headers, records, _row_table_to_markdown(headers, records)


def _html_tables_in(text: str) -> list[tuple[list[str], list[dict[str, str]], str]]:
    """Parse HTML tables within text blocks using a lightweight HTMLParser helper."""
    if "<table" not in text.lower() or "</table" not in text.lower():
        return []
    parser = _TableHTMLParser()
    try:
        parser.feed(text)
    except Exception:
        return []
    output: list[tuple[list[str], list[dict[str, str]], str]] = []
    for table in parser.tables:
        if len(table) < 2:
            continue
        width = max(len(row) for row in table)
        rows = [row + [""] * (width - len(row)) for row in table]
        headers = [str(rows[0][i] or f"column_{i + 1}") for i in range(width)]
        records = [
            {headers[i]: str(row[i]) for i in range(width)}
            for row in rows[1:]
            if any(cell.strip() for cell in row)
        ]
        if records:
            output.append((headers, records, _row_table_to_markdown(headers, records)))
    return output


def _best_table_in_text(text: str) -> tuple[list[str], list[dict[str, str]], str] | None:
    """Find the table in text that yields the highest count of structured data rows."""
    candidates = [*_markdown_tables_in(text), *_html_tables_in(text)]
    if not candidates:
        return None
    return max(candidates, key=lambda table: len(table[1]))


def _row_table_to_markdown(headers: list[str], records: list[dict[str, str]]) -> str:
    """Format a list of header strings and key-value records into a markdown pipe-table string."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for record in records:
        lines.append("| " + " | ".join(record.get(header, "") for header in headers) + " |")
    return "\n".join(lines)


class _TableHTMLParser(__HTML_PARSER_BASE):
    """HTML parser helper extracting tabular row contents from raw table structures."""
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
            self._current_row.append(re.sub(r"\s+", " ", " ".join(self._current_cell)).strip())
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


def _full_document_text(result: ParserRunResult) -> str:
    """Concatenate the entire text contents of the parser result into a single string, stripping page tags."""
    if result.raw_text:
        return _strip_page_markers(result.raw_text)
    blocks = result.structured_preview.get("blocks")
    if isinstance(blocks, list):
        parts = [str(b.get("text") or b.get("text_preview") or "") for b in blocks if isinstance(b, dict)]
        if parts:
            return "\n".join(parts)
    return result.text_preview or ""


def _page_segments(result: ParserRunResult) -> list[tuple[int, str]]:
    """Group parser text blocks or strings by page number boundaries."""
    raw = result.raw_text or ""
    markers = list(_PAGE_MARKER.finditer(raw)) if raw else []
    if markers:
        segments: list[tuple[int, str]] = []
        for index, marker in enumerate(markers):
            page = int(marker.group(1))
            start = marker.end()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(raw)
            segments.append((page, raw[start:end]))
        return segments

    pages = result.structured_preview.get("pages")
    if isinstance(pages, list) and pages:
        out: list[tuple[int, str]] = []
        for entry in pages:
            if not isinstance(entry, dict):
                continue
            page = _safe_int(entry.get("page"), 1)
            text = str(entry.get("text_preview") or entry.get("text") or "").strip()
            if text:
                out.append((page, text))
        if out:
            return out

    blocks = result.structured_preview.get("blocks")
    if isinstance(blocks, list):
        grouped: dict[int, list[str]] = {}
        for block in blocks:
            if not isinstance(block, dict):
                continue
            page = _safe_int(block.get("page"), 1)
            text = str(block.get("text") or block.get("text_preview") or "")
            if text:
                grouped.setdefault(page, []).append(text)
        if grouped:
            return [(page, "\n".join(parts)) for page, parts in sorted(grouped.items())]

    if raw.strip():
        return [(1, raw)]
    return []


def _strip_page_markers(text: str) -> str:
    """Remove HTML-like page marker strings from text."""
    return _PAGE_MARKER.sub("", text)


def _row_to_self_describing_markdown(header: list[str], row: dict[str, str]) -> str:
    """Compile headers and a single row of cells into a self-describing 3-line markdown table."""
    header_line = "| " + " | ".join(header) + " |"
    separator = "| " + " | ".join(["---"] * len(header)) + " |"
    data_line = "| " + " | ".join(str(row.get(col, "")) for col in header) + " |"
    return "\n".join([header_line, separator, data_line])


def _coerce_rows(value: Any) -> list[dict[str, str]]:
    """Ensure rows are lists of string key-value pairs."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for row in value:
        if isinstance(row, dict):
            out.append({str(k): str(v) for k, v in row.items()})
    return out


def _source_url(item: dict[str, Any]) -> Optional[str]:
    """Retrieve source URLs or image paths referenced within layout block text."""
    provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    direct = item.get("source_url") or item.get("url") or provenance.get("url")
    if isinstance(direct, str) and direct:
        return direct
    text = str(item.get("text") or item.get("text_preview") or "")
    match = re.search(r"!\[[^\]]*]\(([^)]+)\)|(/api/parser-benchmarks/media/\S+)", text)
    return match.group(1).strip() if match else None


def _stable_id(strategy: str, page: int, chunk_type: str, text: str, legacy: Any = None) -> str:
    """Generate a stable cryptographic ID hash for a chunk based on its strategy, page, type, and text content."""
    seed = f"{strategy}|{page}|{chunk_type}|{legacy or ''}|{text[:240]}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:14]
    return f"chk-{strategy}-{page}-{chunk_type}-{digest}"


def _dedupe_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """De-duplicate a list of Chunk objects using their stable chunk IDs."""
    seen: set[str] = set()
    out: list[Chunk] = []
    for chunk in chunks:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        out.append(chunk)
    return out


_TIKTOKEN_ENC = None


def _estimate_tokens(text: str) -> int:
    """Estimate token usage counts using tiktoken's cl100k_base encoder (OpenAI)."""
    if not text:
        return 0
    enc = _get_tiktoken()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text.split()))


def _tokenize(text: str) -> list[str]:
    """Tokenize a string using the tiktoken cl100k_base encoder."""
    enc = _get_tiktoken()
    if enc is not None:
        try:
            return enc.encode(text)
        except Exception:
            pass
    return text.split()


def _detokenize(tokens: list[str]) -> str:
    """Detokenize an array of token IDs or strings back into a readable text string."""
    enc = _get_tiktoken()
    if enc is not None and tokens and isinstance(tokens[0], int):
        try:
            return enc.decode(tokens)
        except Exception:
            pass
    return " ".join(tokens)


def _get_tiktoken():
    """Retrieve the global tiktoken encoder singleton wrapper."""
    global _TIKTOKEN_ENC
    if _TIKTOKEN_ENC is False:
        return None
    if _TIKTOKEN_ENC is not None:
        return _TIKTOKEN_ENC
    try:
        import tiktoken

        _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
        return _TIKTOKEN_ENC
    except Exception:
        _TIKTOKEN_ENC = False
        return None


def _safe_int(value: Any, default: int) -> int:
    """Safely coerce any value to an integer, falling back to a default value on error."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> Optional[float]:
    """Safely coerce any value to a float clamped between 0.0 and 1.0, returning None on error."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(parsed, 1.0))


def chunk_document(
    blocks: list[dict[str, Any]],
    strategy: str = "page-by-page",
    max_pages: int = 10,
    chunk_size: int = 512,
) -> list[dict[str, Any]]:
    """Backward-compatible legacy chunker for older callers."""
    page_by_page = strategy == "page-by-page"
    out: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        page = _safe_int(block.get("page"), 1) if page_by_page else 1
        if page_by_page and page > max_pages:
            continue
        out.append(
            {
                "id": f"chunk-{index}",
                "strategy": strategy,
                "text": block.get("text", ""),
                "page": page,
            }
        )
    return out
