"""Deterministic document map built from parser output.

Produces a structured overview of a parsed document — cover fields, headings,
tables, images, and page text — so that the whole-document context can be passed
to a schema-constrained LLM extractor instead of relying on per-field search.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.models.parser_benchmark import ParserRunResult, ParserStatus

logger = logging.getLogger(__name__)

_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE_RE = re.compile(rf"(?im)(?:dated?\s*[:-]?\s*)?(\b{_MONTH}\s+\d{{4}}\b)")
_DATE_SHORT_RE = re.compile(r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b", re.I)
_TITLE_RE = re.compile(r"(?im)^#{1,3}\s+(.+?)(?:\s*!{2,}\s*|\s*$)")
_ENTITY_RE = re.compile(
    r"(?i)([A-Z][A-Za-z\.\s&]{5,80}?(?:Sdn\s+Bhd|Sdn\.\s*Bhd\.|Berhad|Bhd|Sdn|Inc|Corp|PLC|Ltd|Limited|Group|Holdings?))"
)
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.M)
_TABLE_MARKER = re.compile(r"^\|.*\|$")
_PAGE_MARKER = re.compile(r"<!--\s*page:\s*(\d+)\s*-->", re.I)


@dataclass
class CoverSection:
    """Stores key values parsed from a document cover page."""
    title: str | None = None
    date: str | None = None
    entities: list[str] = field(default_factory=list)


@dataclass
class HeadingNode:
    """A tree structure node representing markdown headers in a document."""
    level: int
    text: str
    page: int
    children: list[HeadingNode] = field(default_factory=list)


@dataclass
class TableEntry:
    """Stores metadata and content of a parsed tabular layout item."""
    page: int
    caption: str | None = None
    nearby_heading: str | None = None
    columns: list[str] | None = None
    rows: list[dict[str, str]] | None = None
    text: str = ""


@dataclass
class ImageEntry:
    """Stores page details and captions of visual page references."""
    page: int
    caption: str = ""
    url: str = ""


@dataclass
class DocumentMap:
    """High-level structural outline of the entire document."""
    cover: CoverSection
    heading_tree: list[HeadingNode]
    tables: list[TableEntry]
    images: list[ImageEntry]
    page_text: dict[int, str]
    raw_text: str = ""
    page_count: int = 0

    def full_text(self, max_chars: int | None = None) -> str:
        """Concatenate all page text segments into a single string."""
        pages = sorted(self.page_text.keys())
        parts = [self.page_text[p] for p in pages]
        text = "\n\n".join(parts)
        if max_chars and len(text) > max_chars:
            text = text[:max_chars]
        return text

    def compressed_context(self, max_chars: int = 24000) -> str:
        """Create a compressed structural outline of headings, tables, cover data, and bounded raw texts.

        Helps fit entire complex multi-page document maps into a single LLM prompt context window.
        """
        sections: list[str] = []

        sections.append("=== COVER PAGE ===")
        if self.cover.title:
            sections.append(f"Title: {self.cover.title}")
        if self.cover.date:
            sections.append(f"Date: {self.cover.date}")
        if self.cover.entities:
            sections.append(f"Entities: {', '.join(self.cover.entities[:5])}")

        if self.heading_tree:
            sections.append("\n=== HEADINGS ===")
            sections.append(self._format_headings(self.heading_tree))

        if self.tables:
            sections.append("\n=== TABLES ===")
            for i, t in enumerate(self.tables[:20]):
                heading = f" near '{t.nearby_heading}'" if t.nearby_heading else ""
                text_preview = t.text[:200] if t.text else "(empty)"
                sections.append(
                    f"  Table {i + 1} (page {t.page}): {t.caption or 'Untitled'}{heading}\n"
                    f"    {text_preview}"
                )

        if self.images:
            sections.append("\n=== IMAGES ===")
            for i, img in enumerate(self.images[:15]):
                sections.append(f"  Image {i + 1} (page {img.page}): {img.caption or 'Untitled'}")

        sections.append("\n=== FULL TEXT (compressed) ===")
        sections.append(self.full_text(max_chars))

        return "\n".join(sections)

    def _format_headings(self, nodes: list[HeadingNode], indent: int = 0) -> str:
        """Recursively format the heading nodes tree into bullet points."""
        lines: list[str] = []
        for node in nodes:
            prefix = "  " * indent
            lines.append(f"{prefix}- [{node.level}] {node.text} (p{node.page})")
            if node.children:
                lines.append(self._format_headings(node.children, indent + 1))
        return "\n".join(lines)


def build_document_map(result: ParserRunResult) -> DocumentMap:
    """Build a DocumentMap directly from the parser execution outputs.

    Extracts cover fields, parses heading hierarchies, identifies tabular regions,
    and lists images with page alignments.
    """
    logger.info(
        "document_map: building doc_map parser=%s pages=%d chars=%d tables=%d images=%d",
        result.library, result.pages, result.chars, result.tables, result.images,
    )
    full_text = result.raw_text or result.text_preview or ""
    page_text = _split_page_text(full_text)
    cover = _extract_cover(page_text.get(1, ""), result)
    headings = _extract_headings(page_text)
    tables = _extract_tables(result, headings)
    images = _extract_images(result, full_text)

    doc_map = DocumentMap(
        cover=cover,
        heading_tree=headings,
        tables=tables,
        images=images,
        page_text=page_text,
        raw_text=full_text,
        page_count=result.pages or len(page_text),
    )
    logger.info(
        "document_map: built title=%s date=%s entities=%d headings=%d tables=%d images=%d pages=%d",
        cover.title, cover.date, len(cover.entities),
        _count_headings(headings), len(tables), len(images),
        doc_map.page_count,
    )
    return doc_map


def _split_page_text(raw_text: str) -> dict[int, str]:
    """Parse text structure to split contents by page numbers using parser page markers."""
    markers = list(_PAGE_MARKER.finditer(raw_text))
    if not markers:
        return {1: raw_text}
    pages: dict[int, str] = {}
    for i, marker in enumerate(markers):
        start = marker.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(raw_text)
        pages[int(marker.group(1))] = raw_text[start:end].strip()
    return pages


def _extract_cover(page1_text: str, result: ParserRunResult | None = None) -> CoverSection:
    """Extract cover page details (title, dates, corporate entity names) from the cover page text."""
    title = None
    m = _TITLE_RE.search(page1_text)
    if m:
        title = m.group(1).strip()[:120]
    else:
        first_line = page1_text.strip().split("\n")[0][:120]
        if first_line and not first_line.startswith("<!--"):
            title = first_line

    date = None
    m = _DATE_RE.search(page1_text)
    if m:
        date = m.group(1)
    else:
        m = _DATE_SHORT_RE.search(page1_text)
        if m:
            date = m.group(1)

    entities: list[str] = []
    for m in _ENTITY_RE.finditer(page1_text):
        entity = m.group(1).strip()
        if entity and entity not in entities:
            entities.append(entity)

    return CoverSection(title=title, date=date, entities=entities)


def _extract_headings(page_text: dict[int, str]) -> list[HeadingNode]:
    """Parse heading tags (#, ##, ###) into a hierarchical tree of HeadingNode items."""
    all_headings: list[tuple[int, int, str]] = []
    for page, text in sorted(page_text.items()):
        for m in _HEADING_RE.finditer(text):
            level = min(len(m.group(1)), 3)
            all_headings.append((page, level, m.group(2).strip()[:120]))

    if not all_headings:
        return []

    root: list[HeadingNode] = []
    stack: list[HeadingNode] = []
    for page, level, text in all_headings:
        node = HeadingNode(level=level, text=text, page=page)
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)
    return root


def _extract_tables(result: ParserRunResult, headings: list[HeadingNode]) -> list[TableEntry]:
    """Locate tables from parser outputs and resolve nearby headings."""
    tables: list[TableEntry] = []
    blocks = result.structured_preview.get("blocks") if isinstance(result.structured_preview, dict) else None
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").lower()
            if block_type != "table":
                continue
            page = int(block.get("page") or 1)
            heading = _nearest_heading(headings, page)
            tables.append(TableEntry(
                page=page,
                caption=str(block.get("text") or block.get("text_preview") or "")[:200],
                nearby_heading=heading,
                columns=list(block.get("columns")) if isinstance(block.get("columns"), list) else None,
                rows=list(block.get("rows")) if isinstance(block.get("rows"), list) else None,
                text=str(block.get("markdown") or block.get("text") or "")[:1000],
            ))
    full_text = result.raw_text or result.text_preview or ""
    for page_num in sorted({int(block.get("page") or 1) for block in blocks} if isinstance(blocks, list) else set()):
        page_text_content = _split_page_text(full_text).get(page_num, "")
        for match in _TABLE_MARKER.finditer(page_text_content):
            line = match.group(0).strip()
            if line not in {t.text for t in tables}:
                heading = _nearest_heading(headings, page_num)
                tables.append(TableEntry(page=page_num, text=line[:1000], nearby_heading=heading))
    return tables


def _extract_images(result: ParserRunResult, full_text: str) -> list[ImageEntry]:
    """Parse image markdown structures to build image list."""
    images: list[ImageEntry] = []
    for page, text in _split_page_text(full_text).items():
        for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", text):
            images.append(ImageEntry(page=page, caption=m.group(1).strip() or "", url=m.group(2).strip()))
    if result.images and not images:
        for i in range(min(result.images or 0, 20)):
            images.append(ImageEntry(page=i + 1, caption=f"Image {i + 1}"))
    return images


def _nearest_heading(headings: list[HeadingNode], page: int) -> str | None:
    """Find the text of the heading closest in page alignment to a target table/image."""
    best = None
    best_dist = 999
    for node in _flatten_headings(headings):
        dist = abs(node.page - page)
        if dist < best_dist:
            best_dist = dist
            best = node.text
    return best


def _flatten_headings(nodes: list[HeadingNode]) -> list[HeadingNode]:
    """Flatten tree heading structures into a 1D list."""
    flat: list[HeadingNode] = []
    for node in nodes:
        flat.append(node)
        flat.extend(_flatten_headings(node.children))
    return flat


def _count_headings(nodes: list[HeadingNode]) -> int:
    """Recursively calculate the total count of headings."""
    return sum(1 + _count_headings(n.children) for n in nodes)


def build_document_map_from_evidence(evidence_items: list[dict[str, Any]]) -> DocumentMap:
    """Reconstruct a DocumentMap outline using a list of database evidence_items dictionaries.

    Organizes chunks back into cover sections, tables, heading trees, and pages.
    """
    logger.info("document_map: building from %d evidence items", len(evidence_items))
    page_text: dict[int, str] = {}
    all_headings_raw: list[tuple[int, int, str]] = []
    tables: list[TableEntry] = []
    images: list[ImageEntry] = []

    for item in evidence_items:
        page = int(item.get("page_number") or item.get("page") or 1)
        text = str(item.get("markdown") or item.get("text") or "")
        source = str(item.get("source_type") or "").lower()

        if source.startswith("text") or source == "page":
            page_text[page] = page_text.get(page, "") + "\n" + text
            for m in _HEADING_RE.finditer(text):
                all_headings_raw.append((page, min(len(m.group(1)), 3), m.group(2).strip()[:120]))

        elif source.startswith("table"):
            metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
            tables.append(TableEntry(
                page=page,
                caption=text[:200],
                columns=list(metadata.get("columns")) if isinstance(metadata.get("columns"), list) else None,
                rows=list(metadata.get("rows")) if isinstance(metadata.get("rows"), list) else None,
                text=text[:1000],
            ))

        elif source in {"image_region", "image", "figure", "chart"}:
            source_url = item.get("source_url") or ""
            if not source_url:
                metadata = item.get("metadata_json") if isinstance(item.get("metadata_json"), dict) else {}
                source_url = metadata.get("source_url") or ""
            images.append(ImageEntry(page=page, caption=text[:200], url=source_url or ""))

    headings = _build_heading_tree(all_headings_raw)
    cover = _extract_cover(page_text.get(1, ""), None)

    return DocumentMap(
        cover=cover,
        heading_tree=headings,
        tables=tables,
        images=images,
        page_text=page_text,
        page_count=len(page_text) or 1,
    )


def _build_heading_tree(raw: list[tuple[int, int, str]]) -> list[HeadingNode]:
    """Iteratively compile a raw 1D heading list into a nested HeadingNode tree."""
    if not raw:
        return []
    root: list[HeadingNode] = []
    stack: list[HeadingNode] = []
    for page, level, text in raw:
        node = HeadingNode(level=level, text=text, page=page)
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)
    return root