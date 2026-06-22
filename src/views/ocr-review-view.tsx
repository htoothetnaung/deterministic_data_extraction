"use client";

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import {
  ScanText,
  Save,
  RotateCcw,
  CheckCircle2,
  FileText,
  Table as TableIcon,
  Heading,
  Type,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  MousePointerClick,
  Pencil,
  Sparkles,
} from "lucide-react";

import { PageHeader } from "@/components/app/page-header";
import { SectionCard, EmptyState } from "@/components/app/section";
import { ConfidenceBadge, Badge } from "@/components/app/badges";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { useIsMobile } from "@/hooks/use-mobile";

import { useNav } from "@/lib/store";
import { ocrApi, documentsApi } from "@/lib/api";
import { pct, confidenceLevel } from "@/lib/format";
import type { OcrBlock, DocumentMetadata } from "@/lib/types";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/* Animation + style helpers                                           */
/* ------------------------------------------------------------------ */

const fade = {
  hidden: { opacity: 0, y: 8 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: Math.min(i * 0.03, 0.3), duration: 0.25, ease: "easeOut" },
  }),
};

type ConfLevel = "high" | "medium" | "low";

const CONF_BORDER: Record<ConfLevel, string> = {
  high: "border-emerald-500/70 bg-emerald-500/[0.06] text-emerald-700 dark:text-emerald-300",
  medium: "border-amber-500/70 bg-amber-500/[0.06] text-amber-700 dark:text-amber-300",
  low: "border-rose-500/70 bg-rose-500/[0.06] text-rose-700 dark:text-rose-300",
};

const CONF_DOT: Record<ConfLevel, string> = {
  high: "bg-emerald-500",
  medium: "bg-amber-500",
  low: "bg-rose-500",
};

const FIELD_ACCENT: Record<ConfLevel, string> = {
  high: "border-l-emerald-500/40",
  medium: "border-l-amber-500 ring-1 ring-amber-500/15",
  low: "border-l-rose-500 ring-1 ring-rose-500/15",
};

/* ------------------------------------------------------------------ */
/* Block parsing helpers                                               */
/* ------------------------------------------------------------------ */

function deriveKeyValue(block: OcrBlock): { label: string; value: string } {
  const d = (block.data ?? {}) as Record<string, unknown>;
  const k = d.key ?? d.label ?? d.field;
  const v = d.value ?? d.text;
  if (typeof k === "string" && typeof v === "string") {
    return { label: k.trim() || "Field", value: v };
  }
  const t = block.text ?? "";
  const idx = t.indexOf(":");
  if (idx > 0) {
    return { label: t.slice(0, idx).trim(), value: t.slice(idx + 1).trim() };
  }
  return { label: "Field", value: t };
}

function deriveTable(block: OcrBlock): { headers: string[]; rows: string[][] } {
  const d = (block.data ?? {}) as Record<string, unknown>;
  let headers: string[] = [];
  let rows: string[][] = [];

  const h = d.headers;
  if (Array.isArray(h) && h.every((x) => typeof x === "string")) {
    headers = h as string[];
  }

  const r = d.rows;
  if (Array.isArray(r)) {
    rows = (r as unknown[][]).map((row) =>
      Array.isArray(row) ? row.map((c) => (c == null ? "" : String(c))) : [],
    );
  }

  // If no explicit headers, try parsing the block text (e.g. "Col1 | Col2 | Col3").
  if (headers.length === 0 && block.text) {
    const t = block.text.trim();
    if (!t.includes("\n") && t.includes("|")) {
      headers = t
        .split("|")
        .map((s) => s.trim())
        .filter(Boolean);
    }
  }

  if (headers.length === 0 && rows.length > 0) {
    headers = rows[0].map((_, i) => `Col ${i + 1}`);
  }

  return { headers, rows };
}

/* ------------------------------------------------------------------ */
/* Small UI sub-components                                             */
/* ------------------------------------------------------------------ */

function Legend() {
  return (
    <div className="flex items-center gap-2.5 text-[10px] text-muted-foreground">
      <span className="flex items-center gap-1">
        <span className={cn("size-2 rounded-full", CONF_DOT.high)} /> High
      </span>
      <span className="flex items-center gap-1">
        <span className={cn("size-2 rounded-full", CONF_DOT.medium)} /> Med
      </span>
      <span className="flex items-center gap-1">
        <span className={cn("size-2 rounded-full", CONF_DOT.low)} /> Low
      </span>
    </div>
  );
}

function BlockTypeIcon({ type }: { type: OcrBlock["type"] }) {
  const cls = "size-3.5 text-muted-foreground";
  if (type === "table") return <TableIcon className={cls} />;
  if (type === "heading") return <Heading className={cls} />;
  if (type === "key_value") return <Type className={cls} />;
  if (type === "image") return <FileText className={cls} />;
  if (type === "signature") return <Pencil className={cls} />;
  return <FileText className={cls} />;
}

function SummaryChip({
  label,
  value,
  tone = "slate",
  hint,
}: {
  label: string;
  value: React.ReactNode;
  tone?: ConfLevel | "slate";
  hint?: string;
}) {
  const dot =
    tone === "slate"
      ? "bg-slate-400"
      : CONF_DOT[tone];
  return (
    <div className="flex min-w-[120px] items-center gap-2.5 rounded-lg border border-border/70 bg-card/80 px-3 py-1.5 shadow-xs">
      <span className={cn("size-2 shrink-0 rounded-full", dot)} />
      <div className="flex flex-col leading-tight">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <span className="text-sm font-semibold tabular-nums text-foreground">
          {value}
          {hint ? (
            <span className="ml-1 text-[10px] font-normal text-muted-foreground">
              {hint}
            </span>
          ) : null}
        </span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Page preview                                                        */
/* ------------------------------------------------------------------ */

function PageCard({
  pageNo,
  pageCount,
  blocks,
  selectedId,
  onSelect,
}: {
  pageNo: number;
  pageCount: number;
  blocks: OcrBlock[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const positioned = blocks.filter((b) => b.bbox && b.bbox.length >= 4);
  const stacked = blocks.filter((b) => !b.bbox || b.bbox.length < 4);

  return (
    <div className="mx-auto w-full max-w-[460px] space-y-2">
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          Page <span className="font-medium text-foreground">{pageNo}</span> of{" "}
          {pageCount}
        </span>
        <span>
          {positioned.length} positioned · {stacked.length} text
        </span>
      </div>

      {/* Mock A4 page */}
      <div className="relative aspect-[1/1.414] w-full overflow-hidden rounded-md bg-white shadow-lg ring-1 ring-border">
        {/* Subtle page grain */}
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_bottom,rgba(15,23,42,0.015),transparent_40%,transparent_85%,rgba(15,23,42,0.02))]" />

        {/* Positioned bbox blocks */}
        {positioned.map((b) => {
          const [x, y, w, h] = b.bbox!;
          const level = confidenceLevel(b.confidence);
          const selected = selectedId === b.id;
          return (
            <button
              key={b.id}
              type="button"
              onClick={() => onSelect(b.id)}
              title={b.text}
              className={cn(
                "absolute overflow-hidden rounded-sm border text-left transition-all",
                CONF_BORDER[level],
                selected
                  ? "z-20 ring-2 ring-primary ring-offset-1 ring-offset-white"
                  : "z-10 hover:z-15 hover:ring-1 hover:ring-primary/50",
              )}
              style={{
                left: `${x * 100}%`,
                top: `${y * 100}%`,
                width: `${w * 100}%`,
                height: `${h * 100}%`,
              }}
            >
              <span className="block px-1 py-0.5 text-[8px] font-medium leading-tight text-slate-700 opacity-90 line-clamp-5">
                {b.text}
              </span>
            </button>
          );
        })}

        {positioned.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-[11px] text-slate-400">
            <FileText className="size-6 opacity-50" />
            <span>No positioned regions on this page</span>
          </div>
        ) : null}
      </div>

      {/* Stacked (no bbox) text blocks */}
      {stacked.length > 0 ? (
        <div className="space-y-1.5 rounded-md border border-dashed border-border bg-muted/30 p-2.5">
          <p className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
            <Type className="size-3" />
            Unlocated text
          </p>
          <div className="space-y-1">
            {stacked.map((b) => {
              const level = confidenceLevel(b.confidence);
              const selected = selectedId === b.id;
              return (
                <button
                  key={b.id}
                  type="button"
                  onClick={() => onSelect(b.id)}
                  className={cn(
                    "block w-full rounded border bg-white/70 px-2 py-1 text-left text-[10px] leading-tight text-slate-700 transition-all",
                    CONF_BORDER[level],
                    selected ? "ring-2 ring-primary" : "hover:bg-white",
                  )}
                >
                  <span className="block truncate">{b.text}</span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function DocumentPreviewPanel({
  pages,
  selectedId,
  onSelect,
}: {
  pages: Array<[number, OcrBlock[]]>;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [activePage, setActivePage] = React.useState(0);
  const pageCount = pages.length;
  const current = pages[activePage];

  // Reset active page when pages change shape.
  React.useEffect(() => {
    if (activePage >= pageCount) setActivePage(0);
  }, [activePage, pageCount]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-border/60 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <div className="flex size-6 items-center justify-center rounded-md bg-primary/10 text-primary">
            <FileText className="size-3.5" />
          </div>
          <h3 className="text-sm font-semibold">Document preview</h3>
        </div>
        <div className="flex items-center gap-3">
          <Legend />
          {pageCount > 1 ? (
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={() => setActivePage((p) => Math.max(0, p - 1))}
                disabled={activePage === 0}
              >
                <ChevronLeft className="size-4" />
              </Button>
              <span className="text-[11px] tabular-nums text-muted-foreground">
                {activePage + 1}/{pageCount}
              </span>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={() => setActivePage((p) => Math.min(pageCount - 1, p + 1))}
                disabled={activePage >= pageCount - 1}
              >
                <ChevronRight className="size-4" />
              </Button>
            </div>
          ) : null}
        </div>
      </div>
      <ScrollArea className="flex-1">
        <div className="p-4">
          {current ? (
            <PageCard
              pageNo={current[0]}
              pageCount={pageCount}
              blocks={current[1]}
              selectedId={selectedId}
              onSelect={onSelect}
            />
          ) : (
            <div className="py-12 text-center text-sm text-muted-foreground">
              No pages to display.
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Field editor                                                        */
/* ------------------------------------------------------------------ */

function BlockEditor({
  block,
  onTextChange,
  onKeyValueChange,
  onCellChange,
}: {
  block: OcrBlock;
  onTextChange: (text: string) => void;
  onKeyValueChange: (label: string, value: string) => void;
  onCellChange: (row: number, col: number, value: string) => void;
}) {
  if (block.type === "key_value") {
    const { label, value } = deriveKeyValue(block);
    return (
      <div className="space-y-1.5">
        <label className="text-xs font-medium text-foreground/80">{label}</label>
        <Input
          value={value}
          onChange={(e) => onKeyValueChange(label, e.target.value)}
          className="h-8 text-sm"
          placeholder="Enter value…"
        />
      </div>
    );
  }

  if (block.type === "table") {
    const { headers, rows } = deriveTable(block);
    if (rows.length === 0) {
      return (
        <Textarea
          value={block.text}
          onChange={(e) => onTextChange(e.target.value)}
          className="min-h-20 max-h-60 text-sm"
        />
      );
    }
    return (
      <div className="overflow-hidden rounded-md border border-border/70">
        <div className="max-h-64 overflow-auto">
          <table className="w-full border-collapse text-xs">
            {headers.length > 0 ? (
              <thead className="sticky top-0 z-10 bg-muted/60 backdrop-blur">
                <tr>
                  {headers.map((h, i) => (
                    <th
                      key={i}
                      className="border-b border-border/70 px-2 py-1.5 text-left font-semibold text-muted-foreground"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
            ) : null}
            <tbody>
              {rows.map((row, ri) => (
                <tr
                  key={ri}
                  className="border-b border-border/40 last:border-0 hover:bg-muted/20"
                >
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      className="border-r border-border/30 px-1 py-0.5 last:border-0"
                    >
                      <input
                        value={cell}
                        onChange={(e) => onCellChange(ri, ci, e.target.value)}
                        className="w-full bg-transparent px-1.5 py-1 text-xs text-foreground outline-none focus:bg-primary/5 focus:ring-1 focus:ring-primary/30"
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // heading | text | image | signature -> textarea
  return (
    <Textarea
      value={block.text}
      onChange={(e) => onTextChange(e.target.value)}
      className={cn(
        "min-h-20 max-h-72 text-sm",
        block.type === "heading" && "font-semibold",
      )}
    />
  );
}

function FieldCard({
  block,
  index,
  selected,
  onTextChange,
  onKeyValueChange,
  onCellChange,
  registerRef,
}: {
  block: OcrBlock;
  index: number;
  selected: boolean;
  onTextChange: (text: string) => void;
  onKeyValueChange: (label: string, value: string) => void;
  onCellChange: (row: number, col: number, value: string) => void;
  registerRef: (el: HTMLDivElement | null) => void;
}) {
  const level = confidenceLevel(block.confidence);
  const isLow = level === "low";

  return (
    <motion.div
      custom={index}
      variants={fade}
      initial="hidden"
      animate="show"
      exit={{ opacity: 0, y: -4 }}
    >
      <div
        ref={registerRef}
        className={cn(
          "rounded-lg border border-border bg-card p-3.5 shadow-sm transition-all",
          "border-l-4",
          FIELD_ACCENT[level],
          selected && "ring-2 ring-primary/60",
        )}
      >
        {/* Header row */}
        <div className="mb-2.5 flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1.5">
            <BlockTypeIcon type={block.type} />
            <span className="truncate text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {block.type.replace(/_/g, " ")}
            </span>
            {block.type === "key_value" ? (
              <span className="truncate text-[11px] text-muted-foreground/70">
                · {deriveKeyValue(block).label}
              </span>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {block.edited ? (
              <Badge tone="amber">
                <Pencil className="size-3" />
                Edited
              </Badge>
            ) : null}
            <ConfidenceBadge level={level} />
          </div>
        </div>

        {/* Editor */}
        <BlockEditor
          block={block}
          onTextChange={onTextChange}
          onKeyValueChange={onKeyValueChange}
          onCellChange={onCellChange}
        />

        {/* Footer */}
        <div className="mt-2.5 flex items-center justify-between text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <MousePointerClick className="size-3" />
            Page {block.page} · {pct(block.confidence)} conf
          </span>
          {isLow ? (
            <span className="flex items-center gap-1 text-rose-600 dark:text-rose-400">
              <AlertTriangle className="size-3" />
              Needs review
            </span>
          ) : block.bbox ? (
            <span>positioned</span>
          ) : null}
        </div>
      </div>
    </motion.div>
  );
}

function FieldsPanel({
  blocks,
  selectedId,
  onTextChange,
  onKeyValueChange,
  onCellChange,
  registerRef,
}: {
  blocks: OcrBlock[];
  selectedId: string | null;
  onTextChange: (id: string, text: string) => void;
  onKeyValueChange: (id: string, label: string, value: string) => void;
  onCellChange: (id: string, row: number, col: number, value: string) => void;
  registerRef: (id: string, el: HTMLDivElement | null) => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <div className="flex size-6 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Pencil className="size-3.5" />
          </div>
          <h3 className="text-sm font-semibold">Extracted fields</h3>
        </div>
        <span className="text-xs text-muted-foreground">{blocks.length} blocks</span>
      </div>
      <ScrollArea className="flex-1">
        <div className="space-y-3 p-4">
          <AnimatePresence initial={false}>
            {blocks.length === 0 ? (
              <div className="py-12 text-center text-sm text-muted-foreground">
                No blocks were extracted.
              </div>
            ) : (
              blocks.map((b, i) => (
                <FieldCard
                  key={b.id}
                  block={b}
                  index={i}
                  selected={selectedId === b.id}
                  onTextChange={(t) => onTextChange(b.id, t)}
                  onKeyValueChange={(label, value) =>
                    onKeyValueChange(b.id, label, value)
                  }
                  onCellChange={(r, c, v) => onCellChange(b.id, r, c, v)}
                  registerRef={(el) => registerRef(b.id, el)}
                />
              ))
            )}
          </AnimatePresence>
        </div>
      </ScrollArea>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Main view                                                           */
/* ------------------------------------------------------------------ */

export function OcrReviewView() {
  const reviewDocumentId = useNav((s) => s.reviewDocumentId);
  const review = useNav((s) => s.review);
  const createTemplateFrom = useNav((s) => s.createTemplateFrom);

  const docId = reviewDocumentId ?? "doc-upl-001";
  const isMobile = useIsMobile();
  const qc = useQueryClient();

  const ocrQ = useQuery({
    queryKey: ["ocr", docId],
    queryFn: () => ocrApi.get(docId),
  });
  const docsQ = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.list(),
  });

  const [blocks, setBlocks] = React.useState<OcrBlock[]>([]);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const fieldRefs = React.useRef<Record<string, HTMLDivElement | null>>({});

  // Sync local blocks when fresh OCR data arrives.
  React.useEffect(() => {
    if (ocrQ.data) {
      setBlocks(ocrQ.data.blocks.map((b) => ({ ...b })));
      setSelectedId(null);
    }
  }, [ocrQ.data]);

  /* ---- Derived stats ---- */
  const dirty = React.useMemo(() => {
    if (!ocrQ.data) return false;
    const orig = ocrQ.data.blocks;
    if (blocks.length !== orig.length) return true;
    return blocks.some((b, i) => {
      const o = orig[i];
      if (!o) return true;
      return (
        b.text !== o.text ||
        b.edited !== o.edited ||
        JSON.stringify(b.data) !== JSON.stringify(o.data)
      );
    });
  }, [blocks, ocrQ.data]);

  const editedCount = React.useMemo(() => {
    if (!ocrQ.data) return 0;
    const orig = ocrQ.data.blocks;
    return blocks.filter((b, i) => {
      const o = orig[i];
      if (!o) return true;
      return (
        b.text !== o.text ||
        JSON.stringify(b.data) !== JSON.stringify(o.data)
      );
    }).length;
  }, [blocks, ocrQ.data]);

  const lowConfCount = React.useMemo(
    () => blocks.filter((b) => confidenceLevel(b.confidence) === "low").length,
    [blocks],
  );

  // Live overall confidence: edited blocks get bumped toward high since a human reviewed them.
  const liveConfidence = React.useMemo(() => {
    if (blocks.length === 0) return ocrQ.data?.overall_confidence ?? 0;
    const sum = blocks.reduce(
      (a, b) => a + (b.edited ? Math.max(b.confidence, 0.95) : b.confidence || 0),
      0,
    );
    return sum / blocks.length;
  }, [blocks, ocrQ.data]);

  /* ---- Mutations ---- */
  const saveMut = useMutation({
    mutationFn: () => ocrApi.update(docId, { blocks }),
    onSuccess: () => {
      toast.success("Changes saved", {
        description: `${blocks.length} blocks updated for ${docId}.`,
      });
      qc.invalidateQueries({ queryKey: ["ocr", docId] });
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : "Unknown error";
      toast.error("Save failed", { description: msg });
    },
  });

  const resetMut = useMutation({
    mutationFn: () => ocrApi.reset(docId),
    onSuccess: () => {
      toast.success("OCR reset", {
        description: "Reverted to the original extraction output.",
      });
      qc.invalidateQueries({ queryKey: ["ocr", docId] });
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : "Unknown error";
      toast.error("Reset failed", { description: msg });
    },
  });

  const approveMut = useMutation({
    mutationFn: () => ocrApi.update(docId, { approved: true, blocks }),
    onSuccess: () => {
      toast.success("Extraction approved", {
        description: "Opening the template builder from this document…",
      });
      qc.invalidateQueries({ queryKey: ["ocr", docId] });
      createTemplateFrom(docId);
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : "Unknown error";
      toast.error("Approve failed", { description: msg });
    },
  });

  const processMut = useMutation({
    mutationFn: () => documentsApi.process(docId),
    onSuccess: () => {
      toast.success("Document queued for processing");
      qc.invalidateQueries({ queryKey: ["ocr", docId] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : "Unknown error";
      toast.error("Processing failed", { description: msg });
    },
  });

  /* ---- Edit handlers ---- */
  const updateBlockText = React.useCallback(
    (id: string, text: string) => {
      setBlocks((prev) =>
        prev.map((b) => (b.id === id ? { ...b, text, edited: true } : b)),
      );
    },
    [],
  );

  const updateKeyValue = React.useCallback(
    (id: string, label: string, value: string) => {
      setBlocks((prev) =>
        prev.map((b) => {
          if (b.id !== id) return b;
          const d = (b.data ?? {}) as Record<string, unknown>;
          return {
            ...b,
            text: `${label}: ${value}`,
            data: { ...d, key: label, value },
            edited: true,
          };
        }),
      );
    },
    [],
  );

  const updateTableCell = React.useCallback(
    (id: string, rIdx: number, cIdx: number, value: string) => {
      setBlocks((prev) =>
        prev.map((b) => {
          if (b.id !== id) return b;
          const { rows } = deriveTable(b);
          const newRows = rows.map((row, ri) =>
            ri === rIdx
              ? row.map((cell, ci) => (ci === cIdx ? value : cell))
              : row,
          );
          const d = (b.data ?? {}) as Record<string, unknown>;
          return { ...b, data: { ...d, rows: newRows }, edited: true };
        }),
      );
    },
    [],
  );

  const handleBlockSelect = React.useCallback((id: string) => {
    setSelectedId(id);
    const el = fieldRefs.current[id];
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("ring-2", "ring-primary/70");
      window.setTimeout(() => {
        el.classList.remove("ring-2", "ring-primary/70");
      }, 1400);
    }
  }, []);

  const registerRef = React.useCallback(
    (id: string, el: HTMLDivElement | null) => {
      fieldRefs.current[id] = el;
    },
    [],
  );

  /* ---- Group blocks by page ---- */
  const pages = React.useMemo(() => {
    const m = new Map<number, OcrBlock[]>();
    blocks.forEach((b) => {
      const p = b.page ?? 1;
      if (!m.has(p)) m.set(p, []);
      m.get(p)!.push(b);
    });
    return Array.from(m.entries()).sort((a, b) => a[0] - b[0]);
  }, [blocks]);

  const docs = docsQ.data ?? [];
  const ocr = ocrQ.data;
  const overallLevel = ocr ? confidenceLevel(ocr.overall_confidence) : "medium";

  /* ---- Select items (ensure current docId is represented) ---- */
  const selectItems: DocumentMetadata[] = React.useMemo(() => {
    if (docs.length === 0) {
      return [
        {
          id: docId,
          name: docId,
          type: "other",
          source: "upload",
          mime_type: "",
          size_bytes: 0,
          page_count: 0,
          status: "uploaded",
          tags: [],
          collection: null,
          uploaded_at: new Date().toISOString(),
          processed_at: null,
          preview_url: null,
          confidence: null,
          notes: null,
        },
      ];
    }
    if (docs.some((d) => d.id === docId)) return docs;
    return [
      {
        id: docId,
        name: docId,
        type: "other",
        source: "upload",
        mime_type: "",
        size_bytes: 0,
        page_count: 0,
        status: "ocr_done",
        tags: [],
        collection: null,
        uploaded_at: new Date().toISOString(),
        processed_at: null,
        preview_url: null,
        confidence: null,
        notes: null,
      },
      ...docs,
    ];
  }, [docs, docId]);

  /* ---- Loading state ---- */
  if (ocrQ.isLoading) {
    return (
      <div className="mx-auto w-full max-w-7xl space-y-5">
        <PageHeader
          eyebrow="Step 2 · Review"
          title="Extraction Review"
          description="Verify and correct extracted fields before locking in a template."
          icon={<ScanText className="size-5" />}
        />
        <Skeleton className="h-9 w-72" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full rounded-lg" />
          ))}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <Skeleton className="h-[560px] w-full rounded-xl" />
          <Skeleton className="h-[560px] w-full rounded-xl" />
        </div>
      </div>
    );
  }

  /* ---- No OCR data (not yet processed) ---- */
  if (!ocr) {
    return (
      <div className="mx-auto w-full max-w-7xl space-y-5">
        <PageHeader
          eyebrow="Step 2 · Review"
          title="Extraction Review"
          description="Verify and correct extracted fields before locking in a template."
          icon={<ScanText className="size-5" />}
        />
        <SectionCard title="Document not processed" description={`Document ${docId}`}>
          <EmptyState
            icon={<ScanText className="size-5" />}
            title="No OCR output available"
            description="This document hasn't been processed yet. Run OCR extraction to review and correct fields."
            action={
              <Button
                onClick={() => processMut.mutate()}
                disabled={processMut.isPending}
              >
                <Sparkles className="size-4" />
                {processMut.isPending ? "Processing…" : "Process document"}
              </Button>
            }
          />
        </SectionCard>
      </div>
    );
  }

  const previewPanel = (
    <DocumentPreviewPanel
      pages={pages}
      selectedId={selectedId}
      onSelect={handleBlockSelect}
    />
  );
  const fieldsPanel = (
    <FieldsPanel
      blocks={blocks}
      selectedId={selectedId}
      onTextChange={updateBlockText}
      onKeyValueChange={updateKeyValue}
      onCellChange={updateTableCell}
      registerRef={registerRef}
    />
  );

  return (
    <div className="mx-auto w-full max-w-7xl space-y-5">
      <PageHeader
        eyebrow="Step 2 · Review"
        title="Extraction Review"
        description="Verify and correct extracted fields. Click any region on the document to jump to its editable field."
        icon={<ScanText className="size-5" />}
        actions={
          <>
            <Tooltip>
              <TooltipTrigger asChild>
                <span tabIndex={0}>
                  <ConfidenceBadge level={overallLevel} />
                </span>
              </TooltipTrigger>
              <TooltipContent>
                Overall OCR confidence: {pct(ocr.overall_confidence)} ({ocr.engine})
              </TooltipContent>
            </Tooltip>
            <Button
              variant="outline"
              onClick={() => resetMut.mutate()}
              disabled={resetMut.isPending}
            >
              <RotateCcw className="size-4" />
              Reset
            </Button>
            <Button
              variant="outline"
              onClick={() => saveMut.mutate()}
              disabled={!dirty || saveMut.isPending}
            >
              <Save className="size-4" />
              Save
            </Button>
            <Button
              className="bg-emerald-600 text-white shadow-sm hover:bg-emerald-600/90"
              onClick={() => approveMut.mutate()}
              disabled={approveMut.isPending || ocr.approved}
            >
              <CheckCircle2 className="size-4" />
              {ocr.approved ? "Approved" : "Approve"}
            </Button>
          </>
        }
      />

      {/* Document selector + summary strip */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-2">
          <FileText className="size-4 shrink-0 text-muted-foreground" />
          <Select value={docId} onValueChange={(v) => review(v)}>
            <SelectTrigger className="h-9 w-[280px] max-w-full" size="sm">
              <SelectValue placeholder="Select document" />
            </SelectTrigger>
            <SelectContent>
              {selectItems.map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  <span className="truncate">{d.name}</span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    · {d.page_count}p · {d.type}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="hidden text-xs text-muted-foreground sm:inline">
            {ocr.pages} pages · {ocr.language}
          </span>
        </div>

        <div className="flex flex-wrap gap-2">
          <SummaryChip
            label="Blocks"
            value={blocks.length}
            tone="slate"
          />
          <SummaryChip
            label="Edited"
            value={editedCount}
            tone={editedCount > 0 ? "amber" : "slate"}
          />
          <SummaryChip
            label="Low conf."
            value={lowConfCount}
            tone={lowConfCount > 0 ? "low" : "high"}
          />
          <SummaryChip
            label="Confidence"
            value={pct(liveConfidence)}
            tone={confidenceLevel(liveConfidence)}
          />
        </div>
      </div>

      {/* Main side-by-side area */}
      {isMobile ? (
        <div className="space-y-4">
          <div className="h-[460px] overflow-hidden rounded-xl border border-border/60 bg-card/40">
            {previewPanel}
          </div>
          <div className="h-[640px] overflow-hidden rounded-xl border border-border/60 bg-card/40">
            {fieldsPanel}
          </div>
        </div>
      ) : (
        <ResizablePanelGroup
          direction="horizontal"
          className="h-[640px] rounded-xl border border-border/60 bg-card/40"
        >
          <ResizablePanel defaultSize={52} minSize={35}>
            {previewPanel}
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={48} minSize={35}>
            {fieldsPanel}
          </ResizablePanel>
        </ResizablePanelGroup>
      )}
    </div>
  );
}
