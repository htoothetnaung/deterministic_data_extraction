"use client";

import * as React from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Copy,
  ExternalLink,
  FileJson,
  FileSearch,
  FileText,
  FolderOpen,
  History,
  ImageIcon,
  Loader2,
  PencilLine,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  Settings2,
  Sparkles,
  Table2,
  TextSearch,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { PageHeader } from "@/components/app/page-header";
import { SectionCard, EmptyState } from "@/components/app/section";
import { StatCard } from "@/components/app/stat-card";
import { Badge } from "@/components/app/badges";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { parserBenchmarksApi } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  ParserInfo,
  ParserInputInfo,
  ParserResultDetail,
  ParserRunResult,
  ParserRunResponse,
  ParserRunSummary,
  ParserStatus,
} from "@/lib/types";

const DEFAULT_PREVIEW_CHARS = 2500;
const MAX_SOURCE_OVERLAY_BLOCKS_PER_PAGE = 160;
const EMPTY_INPUTS: ParserInputInfo[] = [];
const EMPTY_PARSERS: ParserInfo[] = [];

type TableSample = {
  page?: number;
  rows?: number;
  columns?: number;
  sample?: string[][];
};

type ParsedBlockType = "heading" | "paragraph" | "table" | "list" | "page" | "code" | "image";

type ParsedBlock = {
  id: string;
  page: number;
  type: ParsedBlockType;
  text: string;
  label: string;
  charStart: number | null;
  charEnd: number | null;
  alignmentSource: "layout_bbox" | "text_span" | "estimated_page" | "unsupported";
  alignmentConfidence: number;
  rect: {
    left: number;
    top: number;
    width: number;
    height: number;
  };
};

type ParserOutputView = "markdown" | "edit" | "blocks" | "json" | "tables";

type TableConfidenceLabel = "high" | "medium" | "low";
type TableSource = "markdown_pipe_table" | "parser_sample" | "noisy_markdown_inferred";

type ParsedMarkdownTable = {
  headers: string[];
  rows: string[][];
  confidence: number;
  confidenceLabel: TableConfidenceLabel;
  risks: string[];
  notes: string[];
  source: TableSource;
};

function parserStatusTone(status: ParserStatus): "emerald" | "amber" | "rose" {
  if (status === "ok") return "emerald";
  if (status === "skipped") return "amber";
  return "rose";
}

function parserStatusLabel(status: ParserStatus): string {
  if (status === "ok") return "OK";
  if (status === "skipped") return "Skipped";
  return "Failed";
}

function inputIcon(input?: ParserInputInfo) {
  if (input?.input_type === "image") return <ImageIcon className="size-4" />;
  return <FileText className="size-4" />;
}

function compatibleParsers(input: ParserInputInfo | undefined, parsers: ParserInfo[]) {
  if (!input) return [];
  return parsers.filter((parser) =>
    parser.supported_input_types.includes(input.input_type),
  );
}

function getTableSamples(result?: ParserRunResult): TableSample[] {
  const value = result?.structured_preview?.table_samples;
  return Array.isArray(value) ? (value as TableSample[]) : [];
}

function blockTypeFor(text: string): ParsedBlockType {
  const trimmed = text.trim();
  if (/!\[[^\]]*]\([^)]+\)|<img\b/i.test(trimmed)) return "image";
  if (/^(#{1,6}\s+|[A-Z][A-Z0-9\s,&()'./-]{5,})$/.test(trimmed.split("\n")[0] ?? "")) {
    return "heading";
  }
  if (/^\|.+\|$/m.test(trimmed)) return "table";
  if (/^(```|~~~)/.test(trimmed)) return "code";
  if (/^(\s*[-*]\s+|\s*\d+[.)]\s+)/m.test(trimmed)) return "list";
  if (/^page\s+\d+$/i.test(trimmed) || /^<\/?page_?number/i.test(trimmed)) return "page";
  return "paragraph";
}

function normalizeBlockType(type: unknown, text: string): ParsedBlockType {
  const value = String(type ?? "").toLowerCase();
  if (value.includes("table")) return "table";
  if (value.includes("image") || value.includes("picture") || value.includes("figure")) return "image";
  if (value.includes("title") || value.includes("heading") || value.includes("sectionheader")) return "heading";
  if (value.includes("list")) return "list";
  if (value.includes("code")) return "code";
  if (value.includes("page")) return "page";
  return blockTypeFor(text);
}

function blockLabel(type: ParsedBlockType): string {
  if (type === "heading") return "HEADING";
  if (type === "table") return "TABLE";
  if (type === "image") return "IMAGE";
  if (type === "list") return "LIST";
  if (type === "code") return "CODE";
  if (type === "page") return "PAGE";
  return "TEXT";
}

function parserSupportsTextAlignment(library: string) {
  const value = library.toLowerCase().replace(/[-\s]/g, "_");
  return (
    value === "pdfplumber" ||
    value === "layout_pdfplumber" ||
    value === "docling" ||
    value === "mistral_ocr" ||
    value === "paddle_ocr" ||
    value.startsWith("paddleocr_vl") ||
    value.startsWith("paddle_ocr_vl")
  );
}

function pageFromText(text: string, fallback: number): number {
  const marker =
    text.match(/(?:^|\n)\s*(?:#{1,3}\s*)?page\s+(\d+)\b/i) ??
    text.match(/(?:^|\n)\s*(\d+)\s*<\/page_?number>/i) ??
    text.match(/(?:^|\n)\s*page[_\s-]?number\s*[:#-]?\s*(\d+)\b/i);
  if (!marker) return fallback;
  const value = Number(marker[1]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function estimateBlockRect(indexOnPage: number, type: ParsedBlockType) {
  const lane = indexOnPage % 5;
  const band = Math.floor(indexOnPage / 5);
  const left = 8 + (lane % 2) * 44;
  const top = Math.min(84, 12 + lane * 13 + band * 8);
  const height = type === "heading" ? 8 : type === "table" ? 18 : 12;
  return {
    left,
    top,
    width: type === "heading" ? 64 : 82 - (lane % 2) * 8,
    height,
  };
}

function parseResultBlocks(text: string, result: ParserRunResult): ParsedBlock[] {
  const structured = blocksFromStructuredPreview(result);
  if (structured.length > 0) return dedupeParsedBlocks(alignBlocksToText(structured, text, result));

  const source = (text || result.text_preview || result.error || "").trim();
  if (!source) return [];

  const chunks = source
    .split(/\n{2,}/)
    .map((chunk) => chunk.trim())
    .filter(Boolean);
  const counters = new Map<number, number>();
  let currentPage = 1;
  const pageTotal = Math.max(result.pages || 1, 1);
  const hasPageMarkers = chunks.some((chunk) => pageFromText(chunk, 0) > 0);

  const blocks: ParsedBlock[] = chunks.map((chunk, index) => {
    currentPage = hasPageMarkers
      ? pageFromText(chunk, currentPage)
      : Math.min(pageTotal, Math.floor((index / Math.max(chunks.length, 1)) * pageTotal) + 1);
    const type = blockTypeFor(chunk);
    const pageIndex = counters.get(currentPage) ?? 0;
    counters.set(currentPage, pageIndex + 1);
    return {
      id: `${result.library}-${currentPage}-${index}`,
      page: currentPage,
      type,
      text: chunk,
      label: blockLabel(type),
      charStart: null,
      charEnd: null,
      alignmentSource: parserSupportsTextAlignment(result.library) ? "text_span" : "unsupported",
      alignmentConfidence: parserSupportsTextAlignment(result.library) ? 0.55 : 0,
      rect: estimateBlockRect(pageIndex, type),
    };
  });
  return dedupeParsedBlocks(alignBlocksToText(blocks, source, result));
}

function blocksFromStructuredPreview(result: ParserRunResult): ParsedBlock[] {
  const value = result.structured_preview?.blocks;
  if (!Array.isArray(value)) return [];
  const rawBlocks = value
    .filter((block): block is Record<string, unknown> => Boolean(block) && typeof block === "object");
  const pageBounds = new Map<number, { width: number; height: number }>();
  for (const block of rawBlocks) {
    const page = Number(block.page) || 1;
    const bbox = block.bbox as Record<string, unknown> | null;
    if (!bbox) continue;
    const width = Number(bbox.x1) || 1;
    const height = Number(bbox.bottom) || 1;
    const existing = pageBounds.get(page) ?? { width: 1, height: 1 };
    pageBounds.set(page, {
      width: Math.max(existing.width, width),
      height: Math.max(existing.height, height),
    });
  }

  return rawBlocks.map((block, index) => {
    const page = Number(block.page) || 1;
    const text = String(block.text ?? block.text_preview ?? "").trim();
    const type = normalizeBlockType(block.type, text);
    const bbox = block.bbox as Record<string, unknown> | null;
    const bounds = pageBounds.get(page) ?? { width: 1, height: 1 };
    const rect = bbox
      ? {
          left: clampPercent((Number(bbox.x0) / bounds.width) * 100),
          top: clampPercent((Number(bbox.top) / bounds.height) * 100),
          width: clampPercent(((Number(bbox.x1) - Number(bbox.x0)) / bounds.width) * 100),
          height: clampPercent(((Number(bbox.bottom) - Number(bbox.top)) / bounds.height) * 100),
        }
      : estimateBlockRect(index, type);
    return {
      id: String(block.id ?? `${result.library}-${page}-${index}`),
      page,
      type,
      text,
      label: blockLabel(type),
      charStart: null,
      charEnd: null,
      alignmentSource: bbox
        ? "layout_bbox"
        : parserSupportsTextAlignment(result.library)
          ? "text_span"
          : "unsupported",
      alignmentConfidence: bbox ? 0.9 : parserSupportsTextAlignment(result.library) ? 0.55 : 0,
      rect,
    };
  });
}

function alignBlocksToText(blocks: ParsedBlock[], sourceText: string, result: ParserRunResult): ParsedBlock[] {
  if (!parserSupportsTextAlignment(result.library)) {
    return blocks.map((block) => ({
      ...block,
      charStart: null,
      charEnd: null,
      alignmentSource: "unsupported",
      alignmentConfidence: 0,
    }));
  }

  let cursor = 0;
  return blocks.map((block) => {
    const range = findTextRange(sourceText, block.text, cursor);
    if (range) {
      cursor = range.end;
      return {
        ...block,
        charStart: range.start,
        charEnd: range.end,
        alignmentSource: block.alignmentSource === "layout_bbox" ? "layout_bbox" : "text_span",
        alignmentConfidence: block.alignmentSource === "layout_bbox" ? 0.92 : 0.72,
      };
    }
    return {
      ...block,
      alignmentSource: block.alignmentSource === "layout_bbox" ? "layout_bbox" : "estimated_page",
      alignmentConfidence: block.alignmentSource === "layout_bbox" ? 0.78 : 0.38,
    };
  });
}

function findTextRange(sourceText: string, blockText: string, startAt: number): { start: number; end: number } | null {
  const source = sourceText || "";
  const needle = blockText.trim();
  if (!source || !needle) return null;

  const exactFromCursor = source.indexOf(needle, Math.max(0, startAt));
  if (exactFromCursor >= 0) return { start: exactFromCursor, end: exactFromCursor + needle.length };

  const exactAnywhere = source.indexOf(needle);
  if (exactAnywhere >= 0) return { start: exactAnywhere, end: exactAnywhere + needle.length };

  const compactNeedle = compactAlignmentText(needle).slice(0, 160);
  if (compactNeedle.length < 24) return null;
  const sourceTail = source.slice(Math.max(0, startAt));
  const compactTail = compactAlignmentText(sourceTail);
  const compactIndex = compactTail.indexOf(compactNeedle);
  if (compactIndex < 0) return null;

  const looseStart = Math.min(source.length - 1, Math.max(0, startAt + compactIndex));
  return { start: looseStart, end: Math.min(source.length, looseStart + needle.length) };
}

function compactAlignmentText(value: string) {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}

function dedupeParsedBlocks(blocks: ParsedBlock[]) {
  const kept: ParsedBlock[] = [];
  const seen = new Set<string>();
  for (const block of blocks) {
    const key = `${block.page}|${block.type}|${normalizedBlockText(block.text)}`;
    if (seen.has(key)) continue;
    if (kept.some((existing) => isDuplicateParsedBlock(block, existing))) continue;
    seen.add(key);
    kept.push(block);
  }
  return kept;
}

function isDuplicateParsedBlock(candidate: ParsedBlock, existing: ParsedBlock) {
  if (candidate.page !== existing.page || candidate.type !== existing.type) return false;
  if (normalizedBlockText(candidate.text) !== normalizedBlockText(existing.text)) return false;
  return rectOverlapRatio(candidate.rect, existing.rect) >= 0.5 || candidate.alignmentSource !== "layout_bbox";
}

function normalizedBlockText(text: string) {
  return text
    .replace(/<[^>]+>/g, " ")
    .replace(/!\[[^\]]*]\([^)]+\)/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase()
    .slice(0, 500);
}

function rectOverlapRatio(a: ParsedBlock["rect"], b: ParsedBlock["rect"]) {
  const left = Math.max(a.left, b.left);
  const top = Math.max(a.top, b.top);
  const right = Math.min(a.left + a.width, b.left + b.width);
  const bottom = Math.min(a.top + a.height, b.top + b.height);
  const intersection = Math.max(0, right - left) * Math.max(0, bottom - top);
  const area = Math.max(0, a.width) * Math.max(0, a.height);
  return area > 0 ? intersection / area : 0;
}

function clampPercent(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

function editorAnchorForPage(
  text: string,
  blocks: ParsedBlock[],
  page: number,
  pageCount: number,
) {
  const pageBlocks = blocks.filter((block) => block.page === page);
  const starts = pageBlocks
    .map((block) => block.charStart)
    .filter((start): start is number => typeof start === "number" && start >= 0);
  if (starts.length > 0) return Math.min(...starts);

  const escapedPage = String(page).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const markerPatterns = [
    new RegExp(`(?:^|\\n)\\s*(?:#{1,3}\\s*)?page\\s+${escapedPage}\\b`, "i"),
    new RegExp(`(?:^|\\n)\\s*${escapedPage}\\s*<\\/page_?number>`, "i"),
    new RegExp(`(?:^|\\n)\\s*page[_\\s-]?number\\s*[:#-]?\\s*${escapedPage}\\b`, "i"),
  ];
  for (const pattern of markerPatterns) {
    const match = pattern.exec(text);
    if (match?.index !== undefined) return Math.max(0, match.index);
  }

  if (pageCount <= 1 || text.length === 0) return 0;
  return Math.floor(text.length * ((page - 1) / pageCount));
}

function supportsPageScopedEditor(library: string) {
  const value = library.toLowerCase().replace(/[-\s]/g, "_");
  return (
    value === "mistral_ocr" ||
    value === "paddleocr_vl_vllm" ||
    value === "paddle_ocr_vllm" ||
    value === "paddle_ocr_vl_vllm"
  );
}

function pageScopedEditState(
  text: string,
  blocks: ParsedBlock[],
  page: number,
  pageCount: number,
  library: string,
) {
  if (!supportsPageScopedEditor(library)) {
    return {
      enabled: false,
      text,
      start: 0,
      end: text.length,
      source: "full_document" as const,
    };
  }

  const markerRange = pageMarkerRange(text, page);
  if (markerRange) {
    return {
      enabled: true,
      text: text.slice(markerRange.start, markerRange.end).trimStart(),
      start: markerRange.start + leadingWhitespaceLength(text.slice(markerRange.start, markerRange.end)),
      end: markerRange.end,
      source: "page_marker" as const,
    };
  }

  const pageBlocks = blocks.filter((block) => block.page === page);
  const starts = pageBlocks
    .map((block) => block.charStart)
    .filter((start): start is number => typeof start === "number" && start >= 0);
  const ends = pageBlocks
    .map((block) => block.charEnd)
    .filter((end): end is number => typeof end === "number" && end >= 0);
  if (starts.length > 0 && ends.length > 0) {
    const start = Math.min(...starts);
    const nextPageStart = blocks
      .filter((block) => block.page > page && typeof block.charStart === "number" && block.charStart > start)
      .map((block) => block.charStart as number)
      .sort((a, b) => a - b)[0];
    return {
      enabled: true,
      text: text.slice(start, nextPageStart ?? Math.max(...ends)).trimStart(),
      start: start + leadingWhitespaceLength(text.slice(start, nextPageStart ?? Math.max(...ends))),
      end: nextPageStart ?? Math.max(...ends),
      source: "text_span" as const,
    };
  }

  const estimatedStart = editorAnchorForPage(text, blocks, page, pageCount);
  const estimatedEnd = page < pageCount ? editorAnchorForPage(text, blocks, page + 1, pageCount) : text.length;
  return {
    enabled: true,
    text: text.slice(estimatedStart, estimatedEnd).trimStart(),
    start: estimatedStart + leadingWhitespaceLength(text.slice(estimatedStart, estimatedEnd)),
    end: estimatedEnd,
    source: "estimated" as const,
  };
}

function pageMarkerRange(text: string, page: number) {
  const markers = [...text.matchAll(/<!--\s*page:\s*(\d+)\s*-->/gi)].map((match) => ({
    page: Number(match[1]),
    markerStart: match.index ?? 0,
    contentStart: (match.index ?? 0) + match[0].length,
  }));
  const index = markers.findIndex((marker) => marker.page === page);
  if (index < 0) return null;
  return {
    start: markers[index].contentStart,
    end: markers[index + 1]?.markerStart ?? text.length,
  };
}

function leadingWhitespaceLength(text: string) {
  return text.length - text.trimStart().length;
}

function replaceTextRange(text: string, start: number, end: number, replacement: string) {
  const boundedStart = Math.min(Math.max(start, 0), text.length);
  const boundedEnd = Math.min(Math.max(end, boundedStart), text.length);
  return `${text.slice(0, boundedStart)}${replacement}${text.slice(boundedEnd)}`;
}

function parseMarkdownTables(markdown: string): ParsedMarkdownTable[] {
  const lines = markdown.split(/\r?\n/);
  const tables: ParsedMarkdownTable[] = [];
  let index = 0;

  while (index < lines.length) {
    const tableLines: string[] = [];
    while (index < lines.length && isMarkdownTableLine(lines[index])) {
      tableLines.push(lines[index]);
      index += 1;
    }
    if (tableLines.length >= 2) {
      const rows = tableLines
        .filter((line) => !isMarkdownSeparatorRow(line))
        .map(splitMarkdownTableRow)
        .filter((row) => row.some((cell) => cell.trim()));
      if (rows.length > 0) {
        const table = buildCleanedTable(rows, markdown, "markdown_pipe_table");
        tables.push(table);
        const looseRows = inferLooseRowsForTable(lines.slice(index), table.headers, markdown);
        if (looseRows.length > table.rows.length) {
          tables.push(buildCleanedTable([table.headers, ...looseRows], markdown, "noisy_markdown_inferred"));
        }
      }
    } else {
      index += 1;
    }
  }

  return dedupeTables(tables);
}

function isMarkdownTableLine(line: string) {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.includes("|", 1);
}

function isMarkdownSeparatorRow(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function splitMarkdownTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.replace(/\\\|/g, "|").trim());
}

function buildCleanedTable(rows: string[][], sourceText: string, source: TableSource): ParsedMarkdownTable {
  const width = Math.max(...rows.map((row) => row.length), 1);
  const normalized = rows
    .map((row) => [...row, ...Array(Math.max(0, width - row.length)).fill("")].slice(0, width))
    .map((row) => row.map(cleanTableCell));
  const header = normalized[0] ?? [];
  const body = normalized.slice(1).filter((row) => row.some(Boolean));
  const compactBody = mergeContinuationRows(body, width).filter((row) => !isDuplicateHeaderRow(row, header));
  return scoreTable(
    {
      headers: header,
      rows: compactBody,
      confidence: 0,
      confidenceLabel: "low",
      risks: [],
      notes: [],
      source,
    },
    sourceText,
  );
}

function cleanTableCell(value: string) {
  return value
    .replace(/\s+/g, " ")
    .replace(/[‐‑‒–—]/g, "-")
    .trim();
}

function mergeContinuationRows(rows: string[][], width: number) {
  const merged: string[][] = [];
  for (const row of rows) {
    const filled = row.filter(Boolean).length;
    const looksLikeContinuation = filled > 0 && filled <= 2 && merged.length > 0 && !hasNumericCell(row);
    if (looksLikeContinuation) {
      const previous = merged[merged.length - 1];
      const targetIndex = row.findIndex(Boolean);
      const index = targetIndex >= 0 ? targetIndex : 0;
      previous[index] = [previous[index], row[index]].filter(Boolean).join(" ");
      continue;
    }
    merged.push([...row, ...Array(Math.max(0, width - row.length)).fill("")].slice(0, width));
  }
  return merged;
}

function inferLooseRowsForTable(lines: string[], headers: string[], sourceText: string) {
  if (headers.length < 3 || !hasFinancialTerms([headers.join(" "), sourceText].join(" "))) return [];
  const rows: string[][] = [];
  const maxRows = 80;
  for (const rawLine of lines) {
    if (rows.length >= maxRows) break;
    const line = cleanTableCell(rawLine);
    if (!line || isMarkdownTableLine(line) || isMarkdownSeparatorRow(line)) continue;
    if (/^(assets|liabilities|company|group|31 december \d{4})$/i.test(line)) {
      rows.push([line, ...Array(headers.length - 1).fill("")]);
      continue;
    }
    const tokens = line.match(/(?:\(?-?\d[\d,]*(?:\.\d+)?\)?|[-–—]|[Nn]il)\b/g) ?? [];
    if (tokens.length < 2) continue;
    const firstToken = tokens[0];
    const tokenStart = firstToken ? line.indexOf(firstToken) : -1;
    const label = tokenStart > 0 ? line.slice(0, tokenStart).trim() : "";
    if (!label || label.length < 3 || label.split(/\s+/).length > 10) continue;
    const row = Array(headers.length).fill("");
    row[0] = label;
    const values = tokens.slice(-Math.min(tokens.length, headers.length - 1));
    values.forEach((value, offset) => {
      row[headers.length - values.length + offset] = value;
    });
    rows.push(row);
  }
  return rows;
}

function hasNumericCell(row: string[]) {
  return row.some((cell) => /(?:\d[\d,]*|[-–—]|nil)/i.test(cell));
}

function isDuplicateHeaderRow(row: string[], header: string[]) {
  const rowText = row.join(" ").toLowerCase();
  const headerText = header.join(" ").toLowerCase();
  return rowText.length > 0 && (rowText === headerText || header.filter(Boolean).every((cell) => rowText.includes(cell.toLowerCase())));
}

function scoreTable(table: ParsedMarkdownTable, sourceText: string): ParsedMarkdownTable {
  const risks = new Set<string>();
  const notes: string[] = [];
  const cellCount = Math.max(table.headers.length * Math.max(table.rows.length, 1), 1);
  const emptyCells = table.rows.flat().filter((cell) => !cell).length;
  const emptyRatio = emptyCells / cellCount;
  let confidence = table.source === "markdown_pipe_table" ? 0.86 : table.source === "parser_sample" ? 0.74 : 0.56;

  if (table.rows.length <= 1) {
    confidence -= 0.16;
    risks.add("low_structure");
    notes.push("Only header/unit rows were detected, so this table may be incomplete.");
  }
  if (emptyRatio > 0.35) {
    confidence -= 0.1;
    risks.add("sparse_cells");
  }
  if (table.source === "noisy_markdown_inferred") {
    risks.add("parser_noise");
    notes.push("Rows were inferred from noisy parser text without inventing missing values.");
  }
  if (hasFinancialTerms([table.headers.join(" "), table.rows.flat().join(" "), sourceText].join(" "))) {
    confidence = Math.min(confidence - 0.12, 0.64);
    risks.add("financial_review");
    notes.push("Finance-related table detected; manual review is required before using values downstream.");
  }

  const bounded = Math.max(0.2, Math.min(0.96, confidence));
  return {
    ...table,
    confidence: bounded,
    confidenceLabel: confidenceLabelFor(bounded),
    risks: Array.from(risks),
    notes,
  };
}

function hasFinancialTerms(text: string) {
  return /\b(financial|assets?|liabilit(?:y|ies)|amorti[sz]ed|fair value|profit|loss|revenue|cash|borrowings?|receivables?|payables?|deposits?|oci|\$'?000|statements?)\b/i.test(
    text,
  );
}

function confidenceLabelFor(confidence: number): TableConfidenceLabel {
  if (confidence >= 0.8) return "high";
  if (confidence >= 0.65) return "medium";
  return "low";
}

function confidenceTone(confidence: number): "emerald" | "amber" | "rose" {
  if (confidence >= 0.8) return "emerald";
  if (confidence >= 0.65) return "amber";
  return "rose";
}

function tableFromSample(sample: TableSample): ParsedMarkdownTable {
  const rows = sample.sample ?? [];
  return scoreTable(
    {
      headers: rows[0] ?? [],
      rows: rows.slice(1),
      confidence: 0,
      confidenceLabel: "low",
      risks: [],
      notes: ["This table came from parser table_samples, not the visible Markdown block."],
      source: "parser_sample",
    },
    rows.flat().join(" "),
  );
}

function dedupeTables(tables: ParsedMarkdownTable[]) {
  const seen = new Set<string>();
  return tables.filter((table) => {
    const key = `${table.headers.join("|")}::${table.rows.slice(0, 6).map((row) => row.join("|")).join("::")}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function outputViewButtonClass(active: boolean) {
  return active ? "bg-muted text-foreground shadow-sm" : "";
}

function metricSummary(run: ParserRunResponse | null) {
  const results = run?.results ?? [];
  const ok = results.filter((result) => result.status === "ok");
  const fastest = ok.reduce<ParserRunResult | null>(
    (best, current) => (!best || current.seconds < best.seconds ? current : best),
    null,
  );
  const largestText = ok.reduce<ParserRunResult | null>(
    (best, current) => (!best || current.chars > best.chars ? current : best),
    null,
  );
  const mostTables = ok.reduce<ParserRunResult | null>(
    (best, current) => (!best || current.tables > best.tables ? current : best),
    null,
  );
  return { results, ok, fastest, largestText, mostTables };
}

export function ParserLabView() {
  const qc = useQueryClient();
  const inputsQ = useQuery({
    queryKey: ["parser-benchmark-inputs"],
    queryFn: () => parserBenchmarksApi.inputs(),
  });
  const parsersQ = useQuery({
    queryKey: ["parser-benchmark-parsers"],
    queryFn: () => parserBenchmarksApi.parsers(),
  });
  const runsQ = useQuery({
    queryKey: ["parser-runs"],
    queryFn: () => parserBenchmarksApi.runs(),
  });

  const inputs = inputsQ.data ?? EMPTY_INPUTS;
  const parsers = parsersQ.data ?? EMPTY_PARSERS;
  const [selectedInputId, setSelectedInputId] = React.useState<string>("");
  const [selectedParserIds, setSelectedParserIds] = React.useState<string[]>([]);
  const [selectedRunId, setSelectedRunId] = React.useState<string>("");
  const [selectedLibrary, setSelectedLibrary] = React.useState<string>("");
  const [processingStartedAt, setProcessingStartedAt] = React.useState<number | null>(null);

  const selectedInput = React.useMemo(
    () => inputs.find((input) => input.id === selectedInputId),
    [inputs, selectedInputId],
  );
  const compatible = React.useMemo(
    () => compatibleParsers(selectedInput, parsers),
    [selectedInput, parsers],
  );
  const compatibleParserIds = React.useMemo(
    () => compatible.map((parser) => parser.id),
    [compatible],
  );
  const compatibleParserKey = compatibleParserIds.join("|");
  const matchingRuns = React.useMemo(
    () => (runsQ.data ?? []).filter((run) => run.input.id === selectedInputId),
    [runsQ.data, selectedInputId],
  );
  const bestMatchingRun = React.useMemo(
    () =>
      matchingRuns.reduce<ParserRunSummary | null>(
        (best, run) => (!best || run.parser_count > best.parser_count ? run : best),
        null,
      ),
    [matchingRuns],
  );
  const runHistoryItems = React.useMemo(() => {
    const matchingIds = new Set(matchingRuns.map((run) => run.run_id));
    return [...matchingRuns, ...(runsQ.data ?? []).filter((run) => !matchingIds.has(run.run_id))];
  }, [matchingRuns, runsQ.data]);

  const runQ = useQuery({
    queryKey: ["parser-run", selectedRunId],
    queryFn: () => parserBenchmarksApi.getRun(selectedRunId),
    enabled: Boolean(selectedRunId),
  });
  const matchingRunDetailsQ = useQueries({
    queries: matchingRuns.slice(0, 20).map((run) => ({
      queryKey: ["parser-run", run.run_id],
      queryFn: () => parserBenchmarksApi.getRun(run.run_id),
      enabled: Boolean(run.run_id),
    })),
  });

  React.useEffect(() => {
    if (selectedInputId || inputs.length === 0) return;
    const annualReport =
      inputs.find((input) => input.name.toLowerCase().includes("hong leong")) ??
      inputs.find((input) => input.input_type === "pdf") ??
      inputs[0];
    setSelectedInputId(annualReport.id);
  }, [inputs, selectedInputId]);

  React.useEffect(() => {
    if (selectedRunId || !selectedInputId || !runsQ.data?.length) return;
    setSelectedRunId((bestMatchingRun ?? runsQ.data[0]).run_id);
  }, [bestMatchingRun, runsQ.data, selectedInputId, selectedRunId]);

  React.useEffect(() => {
    if (!selectedInputId || !selectedRunId || !runsQ.data?.length) return;
    const selectedSummary = runsQ.data.find((run) => run.run_id === selectedRunId);
    if (selectedSummary && selectedSummary.input.id !== selectedInputId && bestMatchingRun) {
      setSelectedRunId(bestMatchingRun.run_id);
    }
  }, [bestMatchingRun, runsQ.data, selectedInputId, selectedRunId]);

  React.useEffect(() => {
    if (!selectedInput || compatible.length === 0) {
      setSelectedParserIds((current) => (current.length === 0 ? current : []));
      return;
    }
    setSelectedParserIds((current) =>
      current.join("|") === compatibleParserKey ? current : compatibleParserIds,
    );
  }, [compatible.length, compatibleParserIds, compatibleParserKey, selectedInput]);

  const runMut = useMutation({
    onMutate: () => {
      setProcessingStartedAt(Date.now());
    },
    mutationFn: () =>
      parserBenchmarksApi.run({
        input_id: selectedInputId,
        parsers: selectedParserIds,
        preview_chars: DEFAULT_PREVIEW_CHARS,
      }),
    onSuccess: (data) => {
      setSelectedRunId(data.run_id);
      setSelectedLibrary(data.results[0]?.library ?? "");
      qc.invalidateQueries({ queryKey: ["parser-runs"] });
      qc.setQueryData(["parser-run", data.run_id], data);
      toast.success("Parser benchmark persisted", {
        description: `Run ${data.run_id} saved to local parser_outputs.`,
      });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Parser benchmark failed");
    },
    onSettled: () => {
      setProcessingStartedAt(null);
    },
  });

  const activeRun = runQ.data ?? null;
  const comparisonResults = React.useMemo(() => {
    const details = matchingRunDetailsQ
      .map((query) => query.data)
      .filter((run): run is ParserRunResponse => Boolean(run))
      .sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
    const latestByLibrary = new Map<string, ParserRunResult>();
    for (const run of details) {
      for (const result of run.results) {
        if (!latestByLibrary.has(result.library)) {
          latestByLibrary.set(result.library, result);
        }
      }
    }
    const parserOrder = new Map(parsers.map((parser, index) => [parser.id, index]));
    return [...latestByLibrary.values()].sort(
      (a, b) => (parserOrder.get(a.library) ?? 999) - (parserOrder.get(b.library) ?? 999),
    );
  }, [matchingRunDetailsQ, parsers]);
  const visibleResults = comparisonResults.length ? comparisonResults : activeRun?.results ?? [];

  React.useEffect(() => {
    if (!visibleResults.length) return;
    const stillExists = visibleResults.some((result) => result.library === selectedLibrary);
    if (!selectedLibrary || !stillExists) {
      setSelectedLibrary(visibleResults[0].library);
    }
  }, [selectedLibrary, visibleResults]);

  const selectedResult = React.useMemo(() => {
    const results = visibleResults;
    return results.find((result) => result.library === selectedLibrary) ?? results[0];
  }, [selectedLibrary, visibleResults]);

  const detailQ = useQuery({
    queryKey: ["parser-result-detail", selectedResult?.run_id, selectedResult?.library],
    queryFn: () => parserBenchmarksApi.getResult(selectedResult!.run_id, selectedResult!.library),
    enabled: Boolean(selectedResult?.run_id && selectedResult?.library),
  });

  const summary = metricSummary(
    activeRun ? { ...activeRun, results: visibleResults } : null,
  );
  const hasInputs = inputs.length > 0;
  const canRun =
    Boolean(selectedInputId) &&
    selectedParserIds.length > 0 &&
    !runMut.isPending &&
    !inputsQ.isLoading &&
    !parsersQ.isLoading;

  function toggleParser(parserId: string, checked: boolean) {
    setSelectedParserIds((current) =>
      checked
        ? Array.from(new Set([...current, parserId]))
        : current.filter((id) => id !== parserId),
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Parser comparison"
        title="Parse Lab"
        description="Benchmark parser libraries, inspect structured Markdown, and link parsed blocks back to the source document."
        icon={<FileSearch className="size-5" />}
        actions={
          <Button onClick={() => runMut.mutate()} disabled={!canRun}>
            {runMut.isPending ? (
              <RefreshCw className="size-4 animate-spin" />
            ) : (
              <Play className="size-4" />
            )}
            Run parsers
          </Button>
        }
      />

      {runMut.isPending ? (
        <ProcessingPanel
          input={selectedInput}
          parsers={parsers}
          selectedParserIds={selectedParserIds}
          startedAt={processingStartedAt}
        />
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(280px,360px)_1fr]">
        <SectionCard
          title="Run Setup"
          description="Select a local input and compatible parser modules."
        >
          {!hasInputs && !inputsQ.isLoading ? (
            <EmptyState
              icon={<AlertTriangle className="size-5" />}
              title="No parser inputs found"
              description="Add PDF or image files under data/ or upload documents first."
            />
          ) : (
            <div className="space-y-5">
              <div className="space-y-2">
                <Label htmlFor="parser-input">Input file</Label>
                <Select value={selectedInputId} onValueChange={setSelectedInputId}>
                  <SelectTrigger id="parser-input" className="w-full">
                    <SelectValue placeholder="Choose a file" />
                  </SelectTrigger>
                  <SelectContent>
                    {inputs.map((input) => (
                      <SelectItem key={input.id} value={input.id}>
                        <span className="flex min-w-0 items-center gap-2">
                          {inputIcon(input)}
                          <span className="truncate">{input.name}</span>
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedInput ? (
                  <div className="flex flex-wrap gap-2 pt-1">
                    <Badge tone="teal">{selectedInput.input_type.toUpperCase()}</Badge>
                    <Badge tone="slate">{formatBytes(selectedInput.size_bytes)}</Badge>
                  </div>
                ) : null}
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3">
                  <Label>Parsers</Label>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelectedParserIds(compatible.map((parser) => parser.id))}
                    disabled={compatible.length === 0}
                  >
                    Select compatible
                  </Button>
                </div>
                <div className="space-y-2">
                  {parsers.map((parser) => {
                    const supported = selectedInput
                      ? parser.supported_input_types.includes(selectedInput.input_type)
                      : false;
                    return (
                      <label
                        key={parser.id}
                        className={cn(
                          "flex items-start gap-3 rounded-lg border border-border/70 px-3 py-2.5",
                          supported ? "bg-background" : "bg-muted/30 opacity-70",
                        )}
                      >
                        <Checkbox
                          checked={selectedParserIds.includes(parser.id)}
                          disabled={!supported}
                          onCheckedChange={(value) => toggleParser(parser.id, value === true)}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium">{parser.name}</span>
                            <Badge tone={parser.installed ? "emerald" : "amber"}>
                              {parser.installed ? "Installed" : "Missing"}
                            </Badge>
                          </span>
                          <span className="mt-1 block text-xs text-muted-foreground">
                            {parser.supported_input_types.join(", ")}
                          </span>
                          {parser.notes ? (
                            <span className="mt-1 block text-xs leading-5 text-amber-700 dark:text-amber-300">
                              {parser.notes}
                            </span>
                          ) : null}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </SectionCard>

        <div className="space-y-4">
          <SectionCard
            title="Run History"
            description="Persisted local parser benchmark runs."
            actions={<History className="size-4 text-muted-foreground" />}
          >
            <div className="flex flex-col gap-3 md:flex-row md:items-center">
              <Select value={selectedRunId} onValueChange={setSelectedRunId}>
                <SelectTrigger className="w-full md:w-[420px]">
                  <SelectValue placeholder="Select a saved run" />
                </SelectTrigger>
                <SelectContent>
                  {runHistoryItems.map((run) => (
                    <SelectItem key={run.run_id} value={run.run_id}>
                      <span className="truncate">
                        {run.input.name} · {formatDate(run.started_at)}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {activeRun &&
              bestMatchingRun &&
              activeRun.run_id !== bestMatchingRun.run_id &&
              activeRun.results.length < bestMatchingRun.parser_count ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSelectedRunId(bestMatchingRun.run_id)}
                >
                  Show {bestMatchingRun.parser_count}-parser run
                </Button>
              ) : null}
              {activeRun ? (
                <div className="flex flex-wrap gap-2">
                  <Badge tone="slate">{activeRun.run_id}</Badge>
                  <Badge tone="teal">{visibleResults.length} latest parser results</Badge>
                </div>
              ) : null}
            </div>
          </SectionCard>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Successful parsers"
              value={`${summary.ok.length}/${summary.results.length || 0}`}
              hint="Completed runs"
              icon={<CheckCircle2 className="size-5" />}
              tone="success"
            />
            <StatCard
              label="Fastest"
              value={summary.fastest ? `${summary.fastest.seconds.toFixed(2)}s` : "--"}
              hint={summary.fastest?.library ?? "Run a benchmark"}
              icon={<Clock3 className="size-5" />}
              tone="primary"
            />
            <StatCard
              label="Most text"
              value={summary.largestText ? summary.largestText.chars.toLocaleString() : "--"}
              hint={summary.largestText?.library ?? "Character count"}
              icon={<TextSearch className="size-5" />}
            />
            <StatCard
              label="Most tables"
              value={summary.mostTables ? summary.mostTables.tables.toLocaleString() : "--"}
              hint={summary.mostTables?.library ?? "Table count"}
              icon={<Table2 className="size-5" />}
              tone="warning"
            />
          </div>
        </div>
      </div>

      <SectionCard title="Parser Results" noBodyPadding>
        {runMut.isPending ? (
          <div className="p-5">
            <EmptyState
              icon={<Loader2 className="size-5 animate-spin" />}
              title="Processing your document"
              description="The backend is running the selected parsers. Results will persist after the run returns."
            />
          </div>
        ) : !visibleResults.length ? (
          <div className="p-5">
            <EmptyState
              icon={<FileSearch className="size-5" />}
              title="No saved parser run selected"
              description="Run a benchmark or choose a saved run from history."
            />
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Parser</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Seconds</TableHead>
                <TableHead className="text-right">Pages</TableHead>
                <TableHead className="text-right">Chars</TableHead>
                <TableHead className="text-right">Tables</TableHead>
                <TableHead className="text-right">Images</TableHead>
                <TableHead>Artifacts</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visibleResults.map((result) => (
                <TableRow
                  key={result.result_id || `${result.run_id}:${result.library}`}
                  className="cursor-pointer"
                  data-state={selectedResult?.library === result.library ? "selected" : undefined}
                  onClick={() => setSelectedLibrary(result.library)}
                >
                  <TableCell className="font-medium">{result.library}</TableCell>
                  <TableCell>
                    <Badge tone={parserStatusTone(result.status)}>
                      {parserStatusLabel(result.status)}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {result.seconds.toFixed(3)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{result.pages}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {result.chars.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{result.tables}</TableCell>
                  <TableCell className="text-right tabular-nums">{result.images}</TableCell>
                  <TableCell className="max-w-[280px] truncate text-muted-foreground">
                    {result.artifact_paths.output_md ?? result.error ?? "--"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </SectionCard>

      {visibleResults.length && selectedResult ? (
        <div className="space-y-4">
          <SectionCard
            title="Library Output"
            description="Switch between the latest persisted result for each parser on this input."
          >
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <Select value={selectedLibrary} onValueChange={setSelectedLibrary}>
                <SelectTrigger className="w-full md:w-[280px]">
                  <SelectValue placeholder="Select parser library" />
                </SelectTrigger>
                <SelectContent>
                  {visibleResults.map((result) => (
                    <SelectItem key={result.result_id || `${result.run_id}:${result.library}`} value={result.library}>
                      {result.library}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <ArtifactPaths result={selectedResult} />
            </div>
          </SectionCard>

          <ResultReviewPanel
            detail={detailQ.data}
            selectedResult={selectedResult}
            loading={detailQ.isLoading}
            onSaved={() => {
              qc.invalidateQueries({ queryKey: ["parser-result-detail", selectedResult.run_id, selectedResult.library] });
              qc.invalidateQueries({ queryKey: ["parser-run", selectedResult.run_id] });
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

function ArtifactPaths({ result }: { result: ParserRunResult }) {
  return (
    <div className="flex min-w-0 flex-wrap gap-2 text-xs">
      {result.artifact_paths.output_md ? (
        <Badge tone="slate">
          <FolderOpen className="size-3" />
          output.md
        </Badge>
      ) : null}
      {result.artifact_paths.structured_json ? (
        <Badge tone="slate">
          <FileJson className="size-3" />
          structured.json
        </Badge>
      ) : null}
      {result.artifact_paths.corrections_json ? (
        <Badge tone="slate">
          <Save className="size-3" />
          corrections.json
        </Badge>
      ) : null}
    </div>
  );
}

function ResultReviewPanel({
  detail,
  selectedResult,
  loading,
  onSaved,
}: {
  detail: ParserResultDetail | undefined;
  selectedResult: ParserRunResult;
  loading: boolean;
  onSaved: () => void;
}) {
  const [correctionText, setCorrectionText] = React.useState("");
  const [correctionNotes, setCorrectionNotes] = React.useState("");
  const [activeBlockId, setActiveBlockId] = React.useState("");
  const [selectedBlockId, setSelectedBlockId] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [zoom, setZoom] = React.useState(100);

  React.useEffect(() => {
    if (!detail) return;
    setCorrectionText(detail.corrections.corrected_text || detail.full_text);
    setCorrectionNotes(detail.corrections.notes);
  }, [detail]);

  const saveCorrectionMut = useMutation({
    mutationFn: () => {
      if (!detail) throw new Error("No parser detail loaded");
      return parserBenchmarksApi.saveCorrections(detail.run.run_id, detail.result.library, {
        ...detail.corrections,
        corrected_text: correctionText,
        notes: correctionNotes,
      });
    },
    onSuccess: () => {
      toast.success("Correction saved");
      onSaved();
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "Failed to save correction");
    },
  });

  const blocks = React.useMemo(
    () => (detail ? parseResultBlocks(correctionText || detail.full_text, detail.result) : []),
    [correctionText, detail],
  );
  const activeBlock = blocks.find((block) => block.id === activeBlockId) ?? null;
  const selectedBlock = blocks.find((block) => block.id === selectedBlockId) ?? null;
  const pageCount = Math.max(
    selectedResult.pages || 1,
    blocks.reduce((max, block) => Math.max(max, block.page), 1),
  );
  const savedCorrectionText = detail?.corrections.corrected_text || detail?.full_text || "";
  const hasUnsavedCorrection =
    Boolean(detail) &&
    (correctionText !== savedCorrectionText || correctionNotes !== (detail?.corrections.notes ?? ""));

  React.useEffect(() => {
    if (activeBlock && activeBlock.page !== page) {
      setPage(activeBlock.page);
    }
  }, [activeBlock, page]);

  if (loading) {
    return (
      <SectionCard title="Result Detail">
        <EmptyState
          icon={<Loader2 className="size-5 animate-spin" />}
          title="Loading parser output"
          description="Reading persisted Markdown, structured JSON, source document, and corrections."
        />
      </SectionCard>
    );
  }

  if (!detail) {
    return (
      <SectionCard title="Result Detail">
        <EmptyState
          icon={<AlertTriangle className="size-5" />}
          title="No parser detail found"
          description="The selected result metadata exists, but its persisted detail could not be loaded."
        />
      </SectionCard>
    );
  }

  return (
    <div className="space-y-4">
      <div className="overflow-hidden rounded-xl border border-border bg-background shadow-sm">
        <div className="flex flex-col gap-3 border-b border-border/70 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 items-center gap-2 text-sm">
            <span className="text-muted-foreground">Parse</span>
            <ChevronRight className="size-4 text-muted-foreground" />
            <span className="truncate font-semibold">{detail.run.input.name}</span>
            <Badge tone={selectedResult.status === "ok" ? "emerald" : parserStatusTone(selectedResult.status)}>
              {parserStatusLabel(selectedResult.status)}
            </Badge>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="slate">{selectedResult.library}</Badge>
            <Badge tone="teal">{selectedResult.chars.toLocaleString()} chars</Badge>
            <Badge tone="slate">{blocks.length} blocks</Badge>
          </div>
        </div>

        <div className="grid min-h-[780px] xl:grid-cols-[minmax(520px,1fr)_minmax(560px,1fr)]">
          <SourceDocumentPanel
            input={detail.run.input}
            page={page}
            pageCount={pageCount}
            zoom={zoom}
            blocks={blocks}
            activeBlock={activeBlock ?? selectedBlock}
            selectedBlockId={selectedBlockId}
            onPageChange={setPage}
            onZoomChange={setZoom}
            onHoverBlock={setActiveBlockId}
            onSelectBlock={setSelectedBlockId}
          />
          <ParsedMarkdownPanel
            result={detail.result}
            blocks={blocks}
            editorText={correctionText}
            editorNotes={correctionNotes}
            hasUnsavedCorrection={hasUnsavedCorrection}
            showingCorrectedDraft={correctionText !== (detail.full_text || "")}
            savingCorrection={saveCorrectionMut.isPending}
            page={page}
            pageCount={pageCount}
            activeBlockId={activeBlockId}
            selectedBlockId={selectedBlockId}
            onPageChange={setPage}
            onHoverBlock={setActiveBlockId}
            onSelectBlock={setSelectedBlockId}
            onEditorTextChange={setCorrectionText}
            onEditorNotesChange={setCorrectionNotes}
            onSaveCorrection={() => saveCorrectionMut.mutate()}
          />
        </div>
      </div>
    </div>
  );
}

function SourceDocumentPanel({
  input,
  page,
  pageCount,
  zoom,
  blocks,
  activeBlock,
  selectedBlockId,
  onPageChange,
  onZoomChange,
  onHoverBlock,
  onSelectBlock,
}: {
  input: ParserInputInfo;
  page: number;
  pageCount: number;
  zoom: number;
  blocks: ParsedBlock[];
  activeBlock: ParsedBlock | null;
  selectedBlockId: string;
  onPageChange: (page: number) => void;
  onZoomChange: (zoom: number) => void;
  onHoverBlock: (blockId: string) => void;
  onSelectBlock: (blockId: string) => void;
}) {
  const [pageImageFailed, setPageImageFailed] = React.useState(false);
  const sourceBlocks = blocks
    .filter((block) => block.page === page && block.alignmentSource === "layout_bbox")
    .slice(0, MAX_SOURCE_OVERLAY_BLOCKS_PER_PAGE);
  const pageHasBlocks = blocks.some((block) => block.page === page);
  const canRenderPage = input.input_type === "pdf" || input.input_type === "image";
  const pageImageUrl = parserBenchmarksApi.pageImageUrl(input.id, input.input_type === "image" ? 1 : page, 1.6);
  const previewUrl = parserBenchmarksApi.previewUrl(input.id);
  const boundedPage = Math.min(Math.max(page, 1), pageCount);
  const fallbackPdfUrl = `${previewUrl}#toolbar=1&navpanes=0&scrollbar=1&page=${boundedPage}&zoom=${zoom}`;

  React.useEffect(() => {
    setPageImageFailed(false);
  }, [input.id, page]);

  return (
    <section className="border-b border-border/70 bg-muted/20 xl:border-b-0 xl:border-r">
      <div className="flex flex-wrap items-center gap-2 border-b border-border/70 bg-background px-4 py-2">
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          disabled={boundedPage <= 1}
          onClick={() => onPageChange(Math.max(1, boundedPage - 1))}
          title="Previous page"
        >
          <ChevronLeft className="size-4" />
        </Button>
        <div className="flex items-center gap-2">
          <input
            value={boundedPage}
            onChange={(event) => {
              const next = Number(event.target.value);
              if (Number.isFinite(next)) onPageChange(Math.min(Math.max(next, 1), pageCount));
            }}
            className="h-9 w-16 rounded-md border border-border bg-background text-center text-sm font-medium tabular-nums"
          />
          <span className="text-sm text-muted-foreground">of {pageCount}</span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          disabled={boundedPage >= pageCount}
          onClick={() => onPageChange(Math.min(pageCount, boundedPage + 1))}
          title="Next page"
        >
          <ChevronRight className="size-4" />
        </Button>
        <div className="mx-2 h-6 w-px bg-border" />
        <Button variant="ghost" size="icon" className="size-8" onClick={() => onZoomChange(Math.max(50, zoom - 10))} title="Zoom out">
          <ZoomOut className="size-4" />
        </Button>
        <span className="min-w-12 text-center text-sm font-medium tabular-nums text-muted-foreground">
          {zoom}%
        </span>
        <Button variant="ghost" size="icon" className="size-8" onClick={() => onZoomChange(Math.min(220, zoom + 10))} title="Zoom in">
          <ZoomIn className="size-4" />
        </Button>
        <Button variant="ghost" size="icon" className="size-8" onClick={() => onZoomChange(100)} title="Reset zoom">
          <RotateCcw className="size-4" />
        </Button>
        <Button variant="ghost" size="icon" className="size-8" onClick={() => window.open(previewUrl, "_blank", "noopener,noreferrer")} title="Open source">
          <ExternalLink className="size-4" />
        </Button>
        <div className="ml-auto min-w-0 text-sm text-muted-foreground">
          <span className="truncate">{input.name}</span>
        </div>
      </div>

      <div className="h-[780px] overflow-auto bg-[#f7f7f8] p-6">
        {canRenderPage && !pageImageFailed ? (
          <div
            className="relative mx-auto origin-top rounded-sm bg-white shadow-sm ring-1 ring-border"
            style={{ width: `${Math.round(680 * (zoom / 100))}px` }}
            onMouseLeave={() => onHoverBlock("")}
          >
            <img
              src={pageImageUrl}
              alt={`${input.name} page ${boundedPage}`}
              className="block w-full select-none"
              draggable={false}
              onError={() => setPageImageFailed(true)}
            />
            <div className="absolute inset-0">
              {sourceBlocks.map((block, index) => {
                const active = block.id === activeBlock?.id || block.id === selectedBlockId;
                return (
                  <button
                    key={`${block.id}-${index}`}
                    type="button"
                    className={cn(
                      "absolute rounded border text-left transition-all",
                      active
                        ? "border-indigo-500 bg-indigo-500/15 shadow-[0_0_0_2px_rgba(99,102,241,0.15)]"
                        : "border-transparent bg-transparent hover:border-indigo-400 hover:bg-indigo-500/10",
                    )}
                    style={{
                      left: `${block.rect.left}%`,
                      top: `${block.rect.top}%`,
                      width: `${block.rect.width}%`,
                      height: `${block.rect.height}%`,
                    }}
                    onMouseEnter={() => onHoverBlock(block.id)}
                    onFocus={() => onHoverBlock(block.id)}
                    onClick={() => onSelectBlock(block.id)}
                    title={block.text.slice(0, 160)}
                  />
                );
              })}
            </div>
            {!activeBlock && sourceBlocks.length > 0 ? (
              <div className="pointer-events-none absolute left-4 top-4 rounded-md border border-border bg-background/90 px-2.5 py-1 text-xs text-muted-foreground shadow-sm">
                Hover parsed blocks to inspect source regions
              </div>
            ) : null}
            {sourceBlocks.length === 0 && pageHasBlocks ? (
              <div className="pointer-events-none absolute left-4 top-4 max-w-[360px] rounded-md border border-amber-200 bg-amber-50/95 px-2.5 py-1 text-xs leading-5 text-amber-900 shadow-sm dark:border-amber-800 dark:bg-amber-950/90 dark:text-amber-100">
                This parser output has text for the page, but no trusted layout bbox for source highlighting.
              </div>
            ) : null}
          </div>
        ) : input.input_type === "pdf" ? (
          <div className="h-full overflow-hidden rounded-lg border border-border bg-background">
            <div className="border-b border-border bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
              Page image rendering is unavailable in this Python environment. Install `pymupdf` for overlay highlights on the real page canvas.
            </div>
            <iframe
              title={`${input.name} page ${boundedPage}`}
              src={fallbackPdfUrl}
              className="h-[730px] w-full border-0"
            />
          </div>
        ) : (
          <EmptyState
            icon={<FileText className="size-5" />}
            title="Page preview not available"
            description="PDF and image inputs can be rendered with linked highlights. Other document types still show parser output."
          />
        )}
      </div>
    </section>
  );
}

function ParsedMarkdownPanel({
  result,
  blocks,
  editorText,
  editorNotes,
  hasUnsavedCorrection,
  showingCorrectedDraft,
  savingCorrection,
  page,
  pageCount,
  activeBlockId,
  selectedBlockId,
  onPageChange,
  onHoverBlock,
  onSelectBlock,
  onEditorTextChange,
  onEditorNotesChange,
  onSaveCorrection,
}: {
  result: ParserRunResult;
  blocks: ParsedBlock[];
  editorText: string;
  editorNotes: string;
  hasUnsavedCorrection: boolean;
  showingCorrectedDraft: boolean;
  savingCorrection: boolean;
  page: number;
  pageCount: number;
  activeBlockId: string;
  selectedBlockId: string;
  onPageChange: (page: number) => void;
  onHoverBlock: (blockId: string) => void;
  onSelectBlock: (blockId: string) => void;
  onEditorTextChange: (value: string) => void;
  onEditorNotesChange: (value: string) => void;
  onSaveCorrection: () => void;
}) {
  const [viewMode, setViewMode] = React.useState<ParserOutputView>("markdown");
  const visibleBlocks = blocks.filter((block) => block.page === page);
  const pageTables = visibleBlocks.flatMap((block) =>
    parseMarkdownTables(block.text).map((table) => ({ block, table })),
  );
  const tableSamples = getTableSamples(result).filter((sample) => !sample.page || sample.page === page);
  const pageEditor = React.useMemo(
    () => pageScopedEditState(editorText, blocks, page, pageCount, result.library),
    [blocks, editorText, page, pageCount, result.library],
  );
  const editorAnchor = React.useMemo(
    () => (pageEditor.enabled ? 0 : editorAnchorForPage(editorText, blocks, page, pageCount)),
    [blocks, editorText, page, pageCount, pageEditor.enabled],
  );

  async function copyPageMarkdown() {
    await navigator.clipboard.writeText(visibleBlocks.map((block) => block.text).join("\n\n"));
    toast.success("Copied page Markdown");
  }

  return (
    <section className="bg-background">
      <div className="flex flex-col gap-3 border-b border-border/70 px-4 py-2 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant={viewMode === "markdown" ? "secondary" : "ghost"}
            size="sm"
            className={cn("h-9", outputViewButtonClass(viewMode === "markdown"))}
            onClick={() => setViewMode("markdown")}
          >
            <FileText className="size-4" />
            Markdown
          </Button>
          <Button
            variant={viewMode === "edit" ? "secondary" : "ghost"}
            size="icon"
            className={cn("size-9", outputViewButtonClass(viewMode === "edit"))}
            title="Edit parser output"
            onClick={() => setViewMode("edit")}
          >
            <PencilLine className="size-4" />
          </Button>
          <Button
            variant={viewMode === "blocks" ? "secondary" : "ghost"}
            size="icon"
            className={cn("size-9", outputViewButtonClass(viewMode === "blocks"))}
            title="Text blocks"
            onClick={() => setViewMode("blocks")}
          >
            <TextSearch className="size-4" />
          </Button>
          <Button
            variant={viewMode === "json" ? "secondary" : "ghost"}
            size="icon"
            className={cn("size-9", outputViewButtonClass(viewMode === "json"))}
            title="Structured JSON"
            onClick={() => setViewMode("json")}
          >
            <FileJson className="size-4" />
          </Button>
          <Button
            variant={viewMode === "tables" ? "secondary" : "ghost"}
            size="icon"
            className={cn("size-9", outputViewButtonClass(viewMode === "tables"))}
            title="Tables"
            onClick={() => setViewMode("tables")}
          >
            <Table2 className="size-4" />
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="icon" className="size-8" disabled={page <= 1} onClick={() => onPageChange(Math.max(1, page - 1))} title="Previous page">
            <ChevronLeft className="size-4" />
          </Button>
          <span className="rounded-md border border-border bg-background px-3 py-1.5 text-sm font-medium tabular-nums">
            {page}
          </span>
          <span className="text-sm text-muted-foreground">of {pageCount}</span>
          <Button variant="ghost" size="icon" className="size-8" disabled={page >= pageCount} onClick={() => onPageChange(Math.min(pageCount, page + 1))} title="Next page">
            <ChevronRight className="size-4" />
          </Button>
          <Button variant="ghost" size="icon" className="size-8" onClick={copyPageMarkdown} title="Copy page Markdown">
            <Copy className="size-4" />
          </Button>
          <Button
            variant={hasUnsavedCorrection ? "default" : "ghost"}
            size="icon"
            className="size-8"
            onClick={onSaveCorrection}
            disabled={savingCorrection}
            title="Save edited parser output"
          >
            {savingCorrection ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
          </Button>
          <Button variant="ghost" size="icon" className="size-8" title="Result settings">
            <Settings2 className="size-4" />
          </Button>
        </div>
      </div>

      <ScrollArea className="h-[780px]">
        <div className="space-y-4 p-6">
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <Badge tone={parserStatusTone(result.status)}>{parserStatusLabel(result.status)}</Badge>
            {showingCorrectedDraft ? <Badge tone="amber">Corrected draft</Badge> : null}
            <span>{result.library}</span>
            <span>/</span>
            <span>{result.seconds.toFixed(3)}s</span>
            <span>/</span>
            <span>{result.tables} tables</span>
          </div>

          {viewMode === "tables" ? (
            <TableView
              page={page}
              tables={pageTables}
              samples={tableSamples}
              onHoverBlock={onHoverBlock}
              onSelectBlock={onSelectBlock}
            />
          ) : viewMode === "edit" ? (
            <InlineParserOutputEditor
              text={pageEditor.text}
              notes={editorNotes}
              page={page}
              pageCount={pageCount}
              pageScoped={pageEditor.enabled}
              pageScopeSource={pageEditor.source}
              editorAnchor={editorAnchor}
              hasUnsavedChanges={hasUnsavedCorrection}
              saving={savingCorrection}
              selectedBlock={blocks.find((block) => block.id === selectedBlockId) ?? null}
              onTextChange={(value) => {
                if (!pageEditor.enabled) {
                  onEditorTextChange(value);
                  return;
                }
                onEditorTextChange(replaceTextRange(editorText, pageEditor.start, pageEditor.end, value));
              }}
              onNotesChange={onEditorNotesChange}
              onSave={onSaveCorrection}
            />
          ) : viewMode === "json" ? (
            <PageJsonView result={result} page={page} blocks={visibleBlocks} />
          ) : viewMode === "blocks" ? (
            <BlockListView
              blocks={visibleBlocks}
              activeBlockId={activeBlockId}
              selectedBlockId={selectedBlockId}
              onHoverBlock={onHoverBlock}
              onSelectBlock={onSelectBlock}
            />
          ) : visibleBlocks.length > 0 ? (
            visibleBlocks.map((block, index) => (
              <ParsedBlockCard
                key={`${block.id}-${index}`}
                block={block}
                active={block.id === activeBlockId}
                selected={block.id === selectedBlockId}
                onHover={onHoverBlock}
                onSelect={onSelectBlock}
              />
            ))
          ) : (
            <EmptyState
              icon={<FileSearch className="size-5" />}
              title="No parsed blocks on this page"
              description="Try another page or select a parser that emits stronger page markers."
            />
          )}
        </div>
      </ScrollArea>
    </section>
  );
}

function InlineParserOutputEditor({
  text,
  notes,
  page,
  pageCount,
  pageScoped,
  pageScopeSource,
  editorAnchor,
  hasUnsavedChanges,
  saving,
  selectedBlock,
  onTextChange,
  onNotesChange,
  onSave,
}: {
  text: string;
  notes: string;
  page: number;
  pageCount: number;
  pageScoped: boolean;
  pageScopeSource: "full_document" | "page_marker" | "text_span" | "estimated";
  editorAnchor: number;
  hasUnsavedChanges: boolean;
  saving: boolean;
  selectedBlock: ParsedBlock | null;
  onTextChange: (value: string) => void;
  onNotesChange: (value: string) => void;
  onSave: () => void;
}) {
  const editorRef = React.useRef<HTMLTextAreaElement>(null);
  const positionedPageRef = React.useRef<number | null>(null);

  React.useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    if (positionedPageRef.current === page) return;
    positionedPageRef.current = page;

    const anchor = Math.min(Math.max(editorAnchor, 0), editor.value.length);
    window.requestAnimationFrame(() => {
      editor.focus({ preventScroll: true });
      editor.setSelectionRange(anchor, anchor);
    });
  }, [editorAnchor, page]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-muted/20 px-3 py-2">
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <Badge tone={hasUnsavedChanges ? "amber" : "emerald"}>
            {hasUnsavedChanges ? "Unsaved edits" : "Saved"}
          </Badge>
          <Badge tone="teal">
            Page {page} of {pageCount}
          </Badge>
          {pageScoped ? (
            <Badge tone={pageScopeSource === "estimated" ? "amber" : "slate"}>
              Page editor
            </Badge>
          ) : null}
          {selectedBlock ? (
            <>
              <Badge tone={selectedBlock.alignmentSource === "layout_bbox" ? "teal" : "slate"}>
                {alignmentLabel(selectedBlock)}
              </Badge>
              <span className="tabular-nums">
                {selectedBlock.charStart !== null && selectedBlock.charEnd !== null
                  ? `chars ${selectedBlock.charStart}-${selectedBlock.charEnd}`
                  : "no text span"}
              </span>
            </>
          ) : (
            <span>Select a parsed block to inspect its source alignment while editing.</span>
          )}
        </div>
        <Button size="sm" onClick={onSave} disabled={saving}>
          {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
          Save edits
        </Button>
      </div>
      <Textarea
        ref={editorRef}
        value={text}
        onChange={(event) => onTextChange(event.target.value)}
        className="min-h-[560px] resize-y font-mono text-xs leading-5"
        spellCheck={false}
        placeholder="Edit parser Markdown output here."
      />
      <Textarea
        value={notes}
        onChange={(event) => onNotesChange(event.target.value)}
        className="min-h-20 text-sm"
        placeholder="Correction notes"
      />
    </div>
  );
}

function alignmentLabel(block: ParsedBlock) {
  if (block.alignmentSource === "layout_bbox") {
    return `Layout aligned ${Math.round(block.alignmentConfidence * 100)}%`;
  }
  if (block.alignmentSource === "text_span") {
    return `Text aligned ${Math.round(block.alignmentConfidence * 100)}%`;
  }
  if (block.alignmentSource === "estimated_page") {
    return "Estimated page region";
  }
  return "Alignment unsupported";
}

function ParsedBlockCard({
  block,
  active,
  selected,
  onHover,
  onSelect,
}: {
  block: ParsedBlock;
  active: boolean;
  selected: boolean;
  onHover: (blockId: string) => void;
  onSelect: (blockId: string) => void;
}) {
  const cleanText = block.text.replace(/^#{1,6}\s+/, "");

  return (
    <article
      className={cn(
        "group cursor-pointer rounded-md border px-3 py-2.5 transition-all",
        active || selected
          ? "border-indigo-300 bg-indigo-50 shadow-sm dark:border-indigo-500/50 dark:bg-indigo-950/25"
          : "border-transparent bg-background hover:border-indigo-200 hover:bg-indigo-50/70 dark:hover:bg-indigo-950/15",
      )}
      onMouseEnter={() => onHover(block.id)}
      onMouseLeave={() => onHover("")}
      onClick={() => onSelect(block.id)}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Page {block.page}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={block.alignmentSource === "layout_bbox" ? "teal" : "slate"}>
            {alignmentLabel(block)}
          </Badge>
          <Badge tone={block.type === "table" ? "teal" : "slate"}>{block.label}</Badge>
        </div>
      </div>
      {containsRenderableMedia(cleanText) ? (
        <MarkdownTextWithMedia text={cleanText} />
      ) : block.type === "heading" ? (
        <h3 className="text-xl font-semibold leading-tight text-foreground">{cleanText}</h3>
      ) : block.type === "table" || block.type === "code" ? (
        <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-sm leading-6 text-foreground">
          {cleanText}
        </pre>
      ) : (
        <MarkdownTextWithMedia text={cleanText} />
      )}
    </article>
  );
}

function MarkdownTextWithMedia({ text }: { text: string }) {
  const normalized = normalizeRenderableMarkdown(text);
  const parts = splitMarkdownMedia(normalized);
  if (parts.length === 1 && parts[0]?.type === "text") {
    return <p className="whitespace-pre-wrap text-[15px] leading-7 text-foreground">{normalized}</p>;
  }
  return (
    <div className="space-y-3 text-[15px] leading-7 text-foreground">
      {parts.map((part, index) =>
        part.type === "image" ? (
          <figure key={`${part.src}-${index}`} className="overflow-hidden rounded-lg border border-border bg-muted/20">
            <img
              src={parserBenchmarksApi.assetUrl(part.src)}
              alt={part.alt}
              className="max-h-[520px] w-full object-contain"
              loading="lazy"
            />
            {part.alt ? (
              <figcaption className="border-t border-border px-3 py-2 text-xs leading-5 text-muted-foreground">
                {part.alt}
              </figcaption>
            ) : null}
          </figure>
        ) : part.type === "html_table" ? (
          <div
            key={index}
            className="overflow-x-auto rounded-lg border border-border bg-background p-2 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1.5 [&_td]:align-top [&_td]:text-xs [&_th]:border [&_th]:border-border [&_th]:bg-muted/50 [&_th]:px-2 [&_th]:py-1.5 [&_th]:text-left [&_th]:text-xs [&_th]:font-semibold"
            dangerouslySetInnerHTML={{ __html: sanitizeTableHtml(part.html) }}
          />
        ) : part.text.trim() ? (
          <p key={index} className="whitespace-pre-wrap">
            {part.text.trim()}
          </p>
        ) : null,
      )}
    </div>
  );
}

function containsRenderableMedia(text: string) {
  return /!\[[^\]]*]\([^)]+\)|<img\b|<table\b/i.test(text);
}

function normalizeRenderableMarkdown(text: string) {
  return text
    .replace(/<div[^>]*>/gi, "\n\n")
    .replace(/<\/div>/gi, "\n\n")
    .replace(/<img\b[^>]*src=["']([^"']+)["'][^>]*>/gi, (_match, src: string) => `![Image](${src})`)
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function splitMarkdownMedia(
  text: string,
): Array<{ type: "text"; text: string } | { type: "image"; alt: string; src: string } | { type: "html_table"; html: string }> {
  const parts: Array<{ type: "text"; text: string } | { type: "image"; alt: string; src: string } | { type: "html_table"; html: string }> = [];
  const pattern = /<table[\s\S]*?<\/table>|!\[([^\]]*)\]\(([^)]+)\)/gi;
  let lastIndex = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > lastIndex) {
      parts.push({ type: "text", text: text.slice(lastIndex, match.index) });
    }
    if (match[0].toLowerCase().startsWith("<table")) {
      parts.push({ type: "html_table", html: match[0] });
    } else {
      parts.push({ type: "image", alt: match[1] ?? "", src: match[2] ?? "" });
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    parts.push({ type: "text", text: text.slice(lastIndex) });
  }
  return parts.length > 0 ? parts : [{ type: "text", text }];
}

function sanitizeTableHtml(html: string) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/\son\w+=["'][^"']*["']/gi, "")
    .replace(/\sstyle=["'][^"']*["']/gi, "");
}

function BlockListView({
  blocks,
  activeBlockId,
  selectedBlockId,
  onHoverBlock,
  onSelectBlock,
}: {
  blocks: ParsedBlock[];
  activeBlockId: string;
  selectedBlockId: string;
  onHoverBlock: (blockId: string) => void;
  onSelectBlock: (blockId: string) => void;
}) {
  if (blocks.length === 0) {
    return (
      <EmptyState
        icon={<TextSearch className="size-5" />}
        title="No text blocks on this page"
        description="Try another page or parser output."
      />
    );
  }

  return (
    <div className="space-y-2">
      {blocks.map((block, index) => (
        <button
          key={`${block.id}-${index}`}
          type="button"
          className={cn(
            "w-full rounded-md border px-3 py-2 text-left transition-all",
            block.id === activeBlockId || block.id === selectedBlockId
              ? "border-indigo-300 bg-indigo-50 dark:border-indigo-500/50 dark:bg-indigo-950/25"
              : "border-border bg-background hover:bg-muted/40",
          )}
          onMouseEnter={() => onHoverBlock(block.id)}
          onMouseLeave={() => onHoverBlock("")}
          onClick={() => onSelectBlock(block.id)}
        >
          <div className="mb-1 flex items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={block.type === "table" ? "teal" : "slate"}>{block.label}</Badge>
              <Badge tone={block.alignmentSource === "layout_bbox" ? "teal" : "slate"}>
                {alignmentLabel(block)}
              </Badge>
            </div>
            {block.alignmentSource === "layout_bbox" ? (
              <span className="text-xs tabular-nums text-muted-foreground">
                {block.rect.left.toFixed(1)}, {block.rect.top.toFixed(1)}
              </span>
            ) : null}
          </div>
          <p className="line-clamp-2 text-sm leading-6 text-foreground">{block.text}</p>
        </button>
      ))}
    </div>
  );
}

function PageJsonView({
  result,
  page,
  blocks,
}: {
  result: ParserRunResult;
  page: number;
  blocks: ParsedBlock[];
}) {
  const payload = {
    parser: result.library,
    page,
    blocks: blocks.map((block) => ({
      id: block.id,
      page: block.page,
      type: block.type,
      label: block.label,
      rect: block.rect,
      char_start: block.charStart,
      char_end: block.charEnd,
      alignment_source: block.alignmentSource,
      alignment_confidence: block.alignmentConfidence,
      text: block.text,
    })),
    table_samples: getTableSamples(result).filter((sample) => !sample.page || sample.page === page),
  };

  return (
    <pre className="overflow-x-auto rounded-lg border border-border bg-muted/20 p-4 text-xs leading-5 text-foreground">
      {JSON.stringify(payload, null, 2)}
    </pre>
  );
}

function TableView({
  page,
  tables,
  samples,
  onHoverBlock,
  onSelectBlock,
}: {
  page: number;
  tables: Array<{ block: ParsedBlock; table: ParsedMarkdownTable }>;
  samples: TableSample[];
  onHoverBlock: (blockId: string) => void;
  onSelectBlock: (blockId: string) => void;
}) {
  if (tables.length === 0 && samples.length === 0) {
    return (
      <EmptyState
        icon={<Table2 className="size-5" />}
        title="No reconstructed tables on this page"
        description="This view needs parser table blocks or table samples for the selected page."
      />
    );
  }

  return (
    <div className="space-y-5">
      {tables.map(({ block, table }, index) => (
        <section
          key={`${block.id}-${index}`}
          className="rounded-lg border border-border bg-background shadow-sm"
          onMouseEnter={() => onHoverBlock(block.id)}
          onMouseLeave={() => onHoverBlock("")}
        >
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="teal">Page {block.page}</Badge>
              <Badge tone="slate">{table.rows.length + 1} rows</Badge>
              <Badge tone="slate">{table.headers.length} cols</Badge>
              <Badge tone={confidenceTone(table.confidence)}>
                {Math.round(table.confidence * 100)}% confidence
              </Badge>
              {table.risks.includes("financial_review") ? <Badge tone="amber">Manual finance review</Badge> : null}
              {table.source === "noisy_markdown_inferred" ? <Badge tone="rose">Noisy reconstruction</Badge> : null}
            </div>
            <Button variant="ghost" size="sm" onClick={() => onSelectBlock(block.id)}>
              Inspect source
            </Button>
          </div>
          <ReconstructedTable table={table} />
        </section>
      ))}

      {samples.length > 0 && tables.length === 0 ? (
        <div className="space-y-4">
          {samples.map((sample, index) => (
            <section key={`${page}-${index}`} className="rounded-lg border border-border bg-background shadow-sm">
              <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
                <Badge tone="teal">Page {sample.page ?? page}</Badge>
                <Badge tone="slate">{sample.rows ?? 0} rows</Badge>
                <Badge tone="slate">{sample.columns ?? 0} cols</Badge>
                <Badge tone={confidenceTone(tableFromSample(sample).confidence)}>
                  {Math.round(tableFromSample(sample).confidence * 100)}% confidence
                </Badge>
              </div>
              <ReconstructedTable table={tableFromSample(sample)} />
            </section>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ReconstructedTable({ table }: { table: ParsedMarkdownTable }) {
  return (
    <div>
      {table.confidence < 0.7 || table.notes.length > 0 ? (
        <div className="border-b border-border bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-800 dark:text-amber-200">
          {table.notes.length > 0
            ? table.notes.join(" ")
            : "Review required before using this reconstructed table downstream."}
        </div>
      ) : null}
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/50">
              {table.headers.map((header, index) => (
                <TableHead
                  key={`${index}-${header}`}
                  className="min-w-28 whitespace-pre-wrap border-r border-border px-3 py-2 align-bottom text-xs font-semibold text-foreground last:border-r-0"
                >
                  {header || "--"}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {table.rows.map((row, rowIndex) => (
              <TableRow key={rowIndex} className={rowIndex % 2 === 0 ? "bg-background" : "bg-muted/20"}>
                {table.headers.map((_, cellIndex) => (
                  <TableCell
                    key={`${rowIndex}-${cellIndex}`}
                    className={cn(
                      "min-w-28 whitespace-pre-wrap border-r border-border px-3 py-2 align-top text-xs leading-5 last:border-r-0",
                      cellIndex > 0 ? "text-right tabular-nums" : "font-medium text-foreground",
                    )}
                  >
                    {row[cellIndex] || ""}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function ProcessingPanel({
  input,
  parsers,
  selectedParserIds,
  startedAt,
}: {
  input: ParserInputInfo | undefined;
  parsers: ParserInfo[];
  selectedParserIds: string[];
  startedAt: number | null;
}) {
  const [now, setNow] = React.useState(() => Date.now());

  React.useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);

  const elapsed = startedAt ? Math.max(0, (now - startedAt) / 1000) : 0;
  const selectedParsers = parsers.filter((parser) => selectedParserIds.includes(parser.id));

  return (
    <section className="overflow-hidden rounded-xl border border-primary/20 bg-background ring-1 ring-inset ring-primary/10">
      <div className="relative px-5 py-4">
        <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-teal-500 via-amber-400 to-primary opacity-80" />
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <div className="relative flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Loader2 className="size-5 animate-spin" />
              <Sparkles className="absolute -right-1 -top-1 size-4 animate-pulse text-amber-500" />
            </div>
            <div className="min-w-0 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-sm font-semibold tracking-tight">
                  Please wait, processing your document
                </h2>
                <span className="flex items-center gap-1 text-primary">
                  <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.2s]" />
                  <span className="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.1s]" />
                  <span className="size-1.5 animate-bounce rounded-full bg-current" />
                </span>
              </div>
              <p className="truncate text-sm text-muted-foreground">
                {input?.name ?? "Selected document"} is running through {selectedParsers.length} parser
                {selectedParsers.length === 1 ? "" : "s"}. Large PDFs can take a while.
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
            <Clock3 className="size-4 text-muted-foreground" />
            <span className="font-medium tabular-nums">{elapsed.toFixed(1)}s</span>
          </div>
        </div>

        <div className="mt-4 h-2 overflow-hidden rounded-full bg-muted">
          <div className="h-full w-2/3 animate-pulse rounded-full bg-gradient-to-r from-teal-500 via-amber-400 to-primary" />
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {selectedParsers.map((parser) => (
            <Badge key={parser.id} tone="teal">
              <Loader2 className="size-3 animate-spin" />
              {parser.name}
            </Badge>
          ))}
        </div>
      </div>
    </section>
  );
}

function StructuredPreview({ result }: { result: ParserRunResult }) {
  const samples = getTableSamples(result);

  if (samples.length > 0) {
    return (
      <ScrollArea className="h-[420px] pr-3">
        <div className="space-y-4">
          {samples.map((sample, index) => (
            <div key={`${sample.page}-${index}`} className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="teal">Page {sample.page ?? "--"}</Badge>
                <Badge tone="slate">{sample.rows ?? 0} rows</Badge>
                <Badge tone="slate">{sample.columns ?? 0} cols</Badge>
              </div>
              <Table>
                <TableBody>
                  {(sample.sample ?? []).map((row, rowIndex) => (
                    <TableRow key={`${index}-${rowIndex}`}>
                      {row.map((cell, cellIndex) => (
                        <TableCell
                          key={`${index}-${rowIndex}-${cellIndex}`}
                          className="max-w-[180px] truncate text-xs"
                        >
                          {cell || "--"}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ))}
        </div>
      </ScrollArea>
    );
  }

  return (
    <ScrollArea className="h-[420px] rounded-lg border border-border bg-muted/20">
      <pre className="whitespace-pre-wrap p-4 text-xs leading-5 text-foreground">
        {JSON.stringify(result.structured_preview, null, 2)}
      </pre>
    </ScrollArea>
  );
}
