"""Multi-granularity chunker for parser output.

Converts a ParserRunResult into retrieval-ready Chunk objects at a chosen
granularity so that downstream retrieval (FTS + pgvector) and field-level
extraction can work against the smallest sufficient evidence unit:

  * document      -> one chunk for the whole document (coarsest)
  * page          -> one chunk per page
  * table_row     -> text blocks stay per-block; every table is decomposed
                     into one self-describing chunk per row (header repeated)
  * sliding_window-> token-aware overlapping windows over the full text
  * block         -> one chunk per cleaned parser block (default, today's behaviour)

Chunks carry traceability metadata (page, bbox, table_index/row_index/header)
so extracted values can be cited back to the exact source location.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from app.models.parser_benchmark import ParserRunResult
from app.services.evidence_cleaner import cleaned_items_for_extraction
from app.services.parsers.base import preview_text

ChunkStrategy = Literal["document", "page", "table_row", "sliding_window", "block"]

DEFAULT_STRATEGY: ChunkStrategy = "page"

_PAGE_MARKER = re.compile(r"<!--\s*page:\s*(\d+)\s*-->", re.IGNORECASE)


@dataclass
class ChunkConfig:
    max_pages: int = 50
    chunk_size: int = 500
    chunk_overlap: int = 80
    min_chunk_chars: int = 1
    max_table_rows: int = 500


@dataclass
class Chunk:
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
        return len(self.text)

    @property
    def text_preview(self) -> str:
        return preview_text(self.text, 1600)

    def to_dict(self, include_text: bool = True) -> dict[str, Any]:
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
    """Chunk a parsed document into retrieval units at the requested granularity."""
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
    items = cleaned_items_for_extraction(result, max_pages=cfg.max_pages)
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
                risk=str(item.get("risk") or "normal"),
                warnings=item.get("warnings") if isinstance(item.get("warnings"), list) else [],
                source_url=_source_url(item),
                columns=[str(c) for c in item.get("columns", [])] if isinstance(item.get("columns"), list) else None,
                rows=_coerce_rows(item.get("rows")),
                strategy="block",
                token_count=_estimate_tokens(text),
                metadata={"source": "evidence_cleaner"},
            )
        )
    return chunks


def _chunk_document(result: ParserRunResult, cfg: ChunkConfig) -> list[Chunk]:
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
    items = cleaned_items_for_extraction(result, max_pages=cfg.max_pages)
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
            header = columns
            for row_index, row in enumerate(rows):
                if emitted_rows >= cfg.max_table_rows:
                    break
                row_text = _row_to_self_describing_markdown(header, row)
                if not row_text.strip():
                    continue
                chunks.append(
                    Chunk(
                        chunk_id=_stable_id(
                            "table_row", page, "table_row",
                            f"{table_counter}:{row_index}:{row_text}",
                        ),
                        page=page,
                        chunk_type="table_row",
                        text=row_text,
                        bbox=bbox,
                        confidence=_safe_float(item.get("confidence")),
                        risk=str(item.get("risk") or "normal"),
                        warnings=item.get("warnings") if isinstance(item.get("warnings"), list) else [],
                        columns=header,
                        rows=[row],
                        table_index=table_counter,
                        row_index=row_index,
                        header=header,
                        strategy="table_row",
                        token_count=_estimate_tokens(row_text),
                        metadata={
                            "source": "evidence_cleaner.table_row",
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
                risk=str(item.get("risk") or "normal"),
                warnings=item.get("warnings") if isinstance(item.get("warnings"), list) else [],
                source_url=_source_url(item),
                columns=columns,
                rows=rows,
                strategy="table_row",
                token_count=_estimate_tokens(text),
                metadata={"source": "evidence_cleaner.block"},
            )
        )
    return chunks


def _chunk_sliding_window(result: ParserRunResult, cfg: ChunkConfig) -> list[Chunk]:
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
            )
            global_index += 1
            if start + size >= len(tokens):
                break
    return chunks or _chunk_document(result, cfg)


def _full_document_text(result: ParserRunResult) -> str:
    if result.raw_text:
        return _strip_page_markers(result.raw_text)
    blocks = result.structured_preview.get("blocks")
    if isinstance(blocks, list):
        parts = [str(b.get("text") or b.get("text_preview") or "") for b in blocks if isinstance(b, dict)]
        if parts:
            return "\n".join(parts)
    return result.text_preview or ""


def _page_segments(result: ParserRunResult) -> list[tuple[int, str]]:
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
    return _PAGE_MARKER.sub("", text)


def _row_to_self_describing_markdown(header: list[str], row: dict[str, str]) -> str:
    header_line = "| " + " | ".join(header) + " |"
    separator = "| " + " | ".join(["---"] * len(header)) + " |"
    data_line = "| " + " | ".join(str(row.get(col, "")) for col in header) + " |"
    return "\n".join([header_line, separator, data_line])


def _coerce_rows(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for row in value:
        if isinstance(row, dict):
            out.append({str(k): str(v) for k, v in row.items()})
    return out


def _source_url(item: dict[str, Any]) -> Optional[str]:
    provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    direct = item.get("source_url") or item.get("url") or provenance.get("url")
    if isinstance(direct, str) and direct:
        return direct
    text = str(item.get("text") or item.get("text_preview") or "")
    match = re.search(r"!\[[^\]]*]\(([^)]+)\)|(/api/parser-benchmarks/media/\S+)", text)
    return match.group(1).strip() if match else None


def _stable_id(strategy: str, page: int, chunk_type: str, text: str, legacy: Any = None) -> str:
    seed = f"{strategy}|{page}|{chunk_type}|{legacy or ''}|{text[:240]}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:14]
    return f"chk-{strategy}-{page}-{chunk_type}-{digest}"


def _dedupe_chunks(chunks: list[Chunk]) -> list[Chunk]:
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
    enc = _get_tiktoken()
    if enc is not None:
        try:
            return enc.encode(text)
        except Exception:
            pass
    return text.split()


def _detokenize(tokens: list[str]) -> str:
    enc = _get_tiktoken()
    if enc is not None and tokens and isinstance(tokens[0], int):
        try:
            return enc.decode(tokens)
        except Exception:
            pass
    return " ".join(tokens)


def _get_tiktoken():
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
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> Optional[float]:
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
    """Backward-compatible chunker kept for any legacy callers.

    Prefer chunk_parser_result() for ParserRunResult inputs.
    """
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
