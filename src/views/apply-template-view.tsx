"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import {
  Layers,
  Play,
  Gauge,
  Search,
  CheckCircle2,
  XCircle,
  Loader2,
  Eye,
  Download,
  ArrowLeft,
  FileStack,
  ChevronRight,
  Clock,
  Target,
  AlertTriangle,
  Zap,
  FileCode2,
  Check,
} from "lucide-react";

import { PageHeader } from "@/components/app/page-header";
import { StatCard } from "@/components/app/stat-card";
import { SectionCard, EmptyState } from "@/components/app/section";
import {
  BatchStatusBadge,
  ConfidenceBadge,
} from "@/components/app/badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import { batchApi, templatesApi, documentsApi } from "@/lib/api";
import { useNav } from "@/lib/store";
import { pct, formatRelative, docTypeLabel } from "@/lib/format";
import type {
  BatchProcessingResult,
  BatchItemResult,
  ExtractionTemplate,
  DocumentMetadata,
  EditableExtractionField,
} from "@/lib/types";

/* ---------------- helpers ---------------- */

const fade = {
  hidden: { opacity: 0, y: 8 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.04, duration: 0.3, ease: "easeOut" },
  }),
};

type SimStatus = "queued" | "processing" | "done" | "failed";

function csvEscape(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const s = String(value);
  if (/[",\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function exportCsv(result: BatchProcessingResult): void {
  const headers = [
    "document_name",
    "status",
    "confidence",
    "matched",
    "mismatched",
    "missing",
    "latency_ms",
  ];
  const rows = result.items.map((it) => [
    it.document_name,
    it.status,
    it.overall_confidence.toFixed(4),
    String(it.matched),
    String(it.mismatched),
    String(it.missing),
    String(it.latency_ms),
  ]);
  const csv = [headers, ...rows]
    .map((row) => row.map(csvEscape).join(","))
    .join("\n");

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `batch-${result.id}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function confidenceLevelFor(score: number): "high" | "medium" | "low" {
  if (score >= 0.9) return "high";
  if (score >= 0.7) return "medium";
  return "low";
}

function formatFieldValue(field: EditableExtractionField): string {
  const v = field.value;
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/* ---------------- main view ---------------- */

type Stage = "config" | "processing" | "results";

export function ApplyTemplateView() {
  const applyTemplateId = useNav((s) => s.applyTemplateId);
  const benchmarkTemplate = useNav((s) => s.benchmarkTemplate);

  const [stage, setStage] = React.useState<Stage>("config");
  const [selectedTemplateId, setSelectedTemplateId] = React.useState<
    string | null
  >(applyTemplateId ?? null);
  const [selectedDocIds, setSelectedDocIds] = React.useState<Set<string>>(
    new Set(),
  );

  // processing simulation state
  const [simStatuses, setSimStatuses] = React.useState<
    Record<string, SimStatus>
  >({});
  const [progress, setProgress] = React.useState(0);

  // results
  const [result, setResult] = React.useState<BatchProcessingResult | null>(
    null,
  );

  // inspect drawer
  const [inspectItem, setInspectItem] = React.useState<BatchItemResult | null>(
    null,
  );

  const templatesQ = useQuery({
    queryKey: ["templates"],
    queryFn: () => templatesApi.list(),
  });
  const docsQ = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.list(),
  });

  const templates = templatesQ.data ?? [];
  const docs = docsQ.data ?? [];

  // default-select first template once loaded (respecting prior applyTemplateId)
  React.useEffect(() => {
    if (!templates.length) return;
    if (!selectedTemplateId) {
      setSelectedTemplateId(applyTemplateId ?? templates[0].id);
    } else if (!templates.some((t) => t.id === selectedTemplateId)) {
      setSelectedTemplateId(applyTemplateId ?? templates[0].id);
    }
  }, [templates, selectedTemplateId, applyTemplateId]);

  const selectedTemplate: ExtractionTemplate | undefined = templates.find(
    (t) => t.id === selectedTemplateId,
  );

  // reset selected docs if template changes (just for clarity)
  // (kept simple: selection persists across template changes)

  /* ----- run batch ----- */
  async function runBatch() {
    if (!selectedTemplateId || selectedDocIds.size === 0) return;
    setStage("processing");
    setProgress(0);

    const docIds = Array.from(selectedDocIds);

    // initialize sim statuses to queued
    const initial: Record<string, SimStatus> = {};
    docIds.forEach((id) => (initial[id] = "queued"));
    setSimStatuses(initial);

    // simulate per-item progress with staggered timeouts, in parallel with the actual API call
    const apiPromise = batchApi.apply({
      template_id: selectedTemplateId,
      document_ids: docIds,
    });

    // stagger: each item flips queued -> processing -> done
    docIds.forEach((id, idx) => {
      const startDelay = 250 + idx * 350;
      const doneDelay = startDelay + 700 + Math.random() * 600;
      window.setTimeout(() => {
        setSimStatuses((prev) => ({ ...prev, [id]: "processing" }));
      }, startDelay);
      window.setTimeout(() => {
        setSimStatuses((prev) => ({ ...prev, [id]: "done" }));
      }, doneDelay);
    });

    // animate overall progress bar 0 -> 95% while waiting, then 100% on resolve
    let p = 0;
    const interval = window.setInterval(() => {
      p = Math.min(95, p + Math.random() * 8 + 3);
      setProgress(p);
    }, 250);

    try {
      const res = await apiPromise;
      window.clearInterval(interval);
      // wait for all sim items to flip to done (max ~3.5s)
      const maxWait = 250 + docIds.length * 350 + 1300;
      await new Promise((r) => setTimeout(r, Math.min(maxWait, 3500)));
      // finalize: mark any still-queued/processing items based on real result
      const finalStatuses: Record<string, SimStatus> = {};
      docIds.forEach((id) => {
        const item = res.items.find((it) => it.document_id === id);
        finalStatuses[id] = item ? item.status : "done";
      });
      setSimStatuses(finalStatuses);
      setProgress(100);
      await new Promise((r) => setTimeout(r, 400));
      setResult(res);
      setStage("results");
      toast.success("Batch extraction complete", {
        description: `${res.done}/${res.total} succeeded · avg confidence ${pct(
          res.average_confidence,
        )}`,
      });
    } catch (err) {
      window.clearInterval(interval);
      toast.error("Batch extraction failed", {
        description: err instanceof Error ? err.message : "Unknown error",
      });
      setStage("config");
    }
  }

  function cancelProcessing() {
    setStage("config");
    setSimStatuses({});
    setProgress(0);
  }

  function backToConfig() {
    setStage("config");
    setResult(null);
    setProgress(0);
    setSimStatuses({});
  }

  /* ----- derived ----- */
  const selectedDocs = docs.filter((d) => selectedDocIds.has(d.id));

  /* ---------------- render ---------------- */
  return (
    <div className="mx-auto w-full max-w-7xl space-y-6">
      <PageHeader
        eyebrow="Step 4 · Batch extraction"
        title="Apply Template"
        description="Run a saved extraction template across multiple similar corporate documents at once. Review per-document results, confidence, and mismatches in a single pass."
        icon={<Layers className="size-5" />}
        actions={
          <Button
            variant="outline"
            disabled={!selectedTemplateId}
            onClick={() => {
              if (selectedTemplateId) benchmarkTemplate(selectedTemplateId);
            }}
          >
            <Gauge className="size-4" />
            Run Benchmark
          </Button>
        }
      />

      <AnimatePresence mode="wait">
        {stage === "config" && (
          <motion.div
            key="config"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.25 }}
            className="space-y-6"
          >
            <ConfigStage
              templates={templates}
              docs={docs}
              templatesLoading={templatesQ.isLoading}
              docsLoading={docsQ.isLoading}
              selectedTemplateId={selectedTemplateId}
              onSelectTemplate={setSelectedTemplateId}
              selectedDocIds={selectedDocIds}
              setSelectedDocIds={setSelectedDocIds}
            />

            {/* sticky summary / run bar */}
            <div className="sticky bottom-4 z-20">
              <Card className="flex flex-col gap-3 border-primary/20 bg-card/95 p-4 shadow-lg backdrop-blur supports-[backdrop-filter]:bg-card/80 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
                  <div className="flex items-center gap-2">
                    <div className="flex size-8 items-center justify-center rounded-md bg-primary/10 text-primary">
                      <FileCode2 className="size-4" />
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Template</p>
                      <p className="max-w-[180px] truncate font-medium">
                        {selectedTemplate?.name ?? "None selected"}
                      </p>
                    </div>
                  </div>
                  <Separator
                    orientation="vertical"
                    className="hidden h-8 w-px bg-border sm:block"
                  />
                  <div className="flex items-center gap-2">
                    <div className="flex size-8 items-center justify-center rounded-md bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                      <FileStack className="size-4" />
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">
                        Documents selected
                      </p>
                      <p className="font-medium tabular-nums">
                        {selectedDocIds.size}
                      </p>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelectedDocIds(new Set())}
                    disabled={selectedDocIds.size === 0}
                  >
                    Clear
                  </Button>
                  <Button
                    onClick={runBatch}
                    disabled={
                      !selectedTemplateId || selectedDocIds.size === 0
                    }
                  >
                    <Play className="size-4" />
                    Run batch extraction
                  </Button>
                </div>
              </Card>
            </div>
          </motion.div>
        )}

        {stage === "processing" && (
          <motion.div
            key="processing"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.25 }}
          >
            <ProcessingStage
              template={selectedTemplate}
              selectedDocs={selectedDocs}
              simStatuses={simStatuses}
              progress={progress}
              onCancel={cancelProcessing}
            />
          </motion.div>
        )}

        {stage === "results" && result && (
          <motion.div
            key="results"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.25 }}
            className="space-y-6"
          >
            <ResultsStage
              result={result}
              template={selectedTemplate}
              onBack={backToConfig}
              onBenchmark={() => {
                if (selectedTemplateId) benchmarkTemplate(selectedTemplateId);
              }}
              onExport={() => exportCsv(result)}
              onInspect={setInspectItem}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Inspect drawer */}
      <InspectSheet
        item={inspectItem}
        open={!!inspectItem}
        onOpenChange={(o) => !o && setInspectItem(null)}
      />
    </div>
  );
}

/* ---------------- Stage 1: Configuration ---------------- */

function ConfigStage({
  templates,
  docs,
  templatesLoading,
  docsLoading,
  selectedTemplateId,
  onSelectTemplate,
  selectedDocIds,
  setSelectedDocIds,
}: {
  templates: ExtractionTemplate[];
  docs: DocumentMetadata[];
  templatesLoading: boolean;
  docsLoading: boolean;
  selectedTemplateId: string | null;
  onSelectTemplate: (id: string) => void;
  selectedDocIds: Set<string>;
  setSelectedDocIds: React.Dispatch<React.SetStateAction<Set<string>>>;
}) {
  const [search, setSearch] = React.useState("");
  const [typeFilter, setTypeFilter] = React.useState<string>("all");

  const filteredDocs = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    return docs.filter((d) => {
      if (typeFilter !== "all" && d.type !== typeFilter) return false;
      if (q && !d.name.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [docs, search, typeFilter]);

  const docTypes = React.useMemo(() => {
    const s = new Set(docs.map((d) => d.type));
    return Array.from(s);
  }, [docs]);

  const corporateIds = React.useMemo(
    () =>
      docs.filter((d) => d.source === "corporate_db").map((d) => d.id),
    [docs],
  );

  function toggleDoc(id: string) {
    setSelectedDocIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAllCorporate() {
    setSelectedDocIds((prev) => {
      const next = new Set(prev);
      corporateIds.forEach((id) => next.add(id));
      return next;
    });
    toast.success(`Added ${corporateIds.length} corporate documents`);
  }

  function clearSelection() {
    setSelectedDocIds(new Set());
  }

  const allFilteredSelected =
    filteredDocs.length > 0 &&
    filteredDocs.every((d) => selectedDocIds.has(d.id));

  function toggleSelectAll() {
    setSelectedDocIds((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        filteredDocs.forEach((d) => next.delete(d.id));
      } else {
        filteredDocs.forEach((d) => next.add(d.id));
      }
      return next;
    });
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
      {/* Template selector */}
      <div className="lg:col-span-2">
        <SectionCard
          title="Select template"
          description="Choose the extraction template to apply"
        >
          {templatesLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-28 w-full" />
              ))}
            </div>
          ) : templates.length === 0 ? (
            <EmptyState
              icon={<FileCode2 className="size-5" />}
              title="No templates yet"
              description="Create a template first to apply it across documents."
            />
          ) : (
            <div className="max-h-[28rem] space-y-2.5 overflow-y-auto pr-1">
              {templates.map((t, i) => {
                const isSelected = t.id === selectedTemplateId;
                return (
                  <motion.div
                    key={t.id}
                    custom={i}
                    variants={fade}
                    initial="hidden"
                    animate="show"
                  >
                    <Card
                      role="button"
                      tabIndex={0}
                      onClick={() => onSelectTemplate(t.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onSelectTemplate(t.id);
                        }
                      }}
                      className={`relative cursor-pointer p-4 transition-all hover:-translate-y-0.5 hover:shadow-md ${
                        isSelected
                          ? "border-primary/60 ring-2 ring-inset ring-primary/40"
                          : "hover:border-primary/30"
                      }`}
                    >
                      {isSelected && (
                        <span className="absolute right-3 top-3 flex size-5 items-center justify-center rounded-full bg-primary text-primary-foreground">
                          <Check className="size-3" />
                        </span>
                      )}
                      <div className="flex items-start gap-3">
                        <div
                          className={`flex size-9 shrink-0 items-center justify-center rounded-lg ${
                            isSelected
                              ? "bg-primary/10 text-primary"
                              : "bg-muted text-muted-foreground"
                          }`}
                        >
                          <FileCode2 className="size-4.5" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-semibold leading-tight">
                            {t.name}
                          </p>
                          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                            {t.description ?? "No description"}
                          </p>
                          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                            <span className="flex items-center gap-1">
                              <Layers className="size-3" />
                              {t.fields.length} fields
                            </span>
                            <span className="flex items-center gap-1">
                              <Target className="size-3" />
                              {t.success_rate !== null
                                ? pct(t.success_rate, 0)
                                : "n/a"}
                            </span>
                            <span className="flex items-center gap-1">
                              <Clock className="size-3" />
                              {t.usage_count} runs
                            </span>
                          </div>
                        </div>
                      </div>
                    </Card>
                  </motion.div>
                );
              })}
            </div>
          )}
        </SectionCard>
      </div>

      {/* Document selector */}
      <div className="lg:col-span-3">
        <SectionCard
          title="Select documents"
          description={`${selectedDocIds.size} selected · ${docs.length} available`}
          actions={
            <div className="flex items-center gap-1.5">
              <Button
                variant="outline"
                size="sm"
                onClick={selectAllCorporate}
              >
                <FileStack className="size-3.5" />
                Select all corporate
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={clearSelection}
                disabled={selectedDocIds.size === 0}
              >
                Clear
              </Button>
            </div>
          }
          noBodyPadding
        >
          {/* Filter bar */}
          <div className="flex flex-col gap-2 border-b border-border/60 p-3 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search documents…"
                className="pl-8"
              />
            </div>
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="sm:w-44">
                <SelectValue placeholder="Type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All types</SelectItem>
                {docTypes.map((t) => (
                  <SelectItem key={t} value={t}>
                    {docTypeLabel(t)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Table */}
          {docsLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : filteredDocs.length === 0 ? (
            <div className="p-4">
              <EmptyState
                icon={<FileStack className="size-5" />}
                title="No documents match"
                description="Adjust your search or filter to find documents to process."
              />
            </div>
          ) : (
            <ScrollArea className="max-h-[26rem]">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableHead className="w-10 pl-4">
                      <Checkbox
                        checked={allFilteredSelected}
                        onCheckedChange={toggleSelectAll}
                        aria-label="Select all visible"
                      />
                    </TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead className="hidden sm:table-cell">Type</TableHead>
                    <TableHead className="hidden md:table-cell">Source</TableHead>
                    <TableHead className="hidden lg:table-cell">Pages</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredDocs.map((d) => {
                    const checked = selectedDocIds.has(d.id);
                    return (
                      <TableRow
                        key={d.id}
                        onClick={() => toggleDoc(d.id)}
                        className={`cursor-pointer ${
                          checked ? "bg-primary/5" : ""
                        }`}
                      >
                        <TableCell
                          className="pl-4"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <Checkbox
                            checked={checked}
                            onCheckedChange={() => toggleDoc(d.id)}
                            aria-label={`Select ${d.name}`}
                          />
                        </TableCell>
                        <TableCell className="max-w-[260px]">
                          <div className="flex items-center gap-2.5">
                            <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                              <FileStack className="size-4" />
                            </div>
                            <div className="min-w-0">
                              <p className="truncate text-sm font-medium">
                                {d.name}
                              </p>
                              <p className="truncate text-xs text-muted-foreground">
                                {docTypeLabel(d.type)} ·{" "}
                                {formatRelative(d.uploaded_at)}
                              </p>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell className="hidden sm:table-cell text-xs text-muted-foreground">
                          {docTypeLabel(d.type)}
                        </TableCell>
                        <TableCell className="hidden md:table-cell">
                          {d.source === "corporate_db" ? (
                            <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                              Corporate DB
                            </span>
                          ) : (
                            <span className="text-xs text-muted-foreground">
                              Upload
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="hidden lg:table-cell text-xs text-muted-foreground tabular-nums">
                          {d.page_count}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </ScrollArea>
          )}
        </SectionCard>
      </div>
    </div>
  );
}

/* ---------------- Stage 2: Processing ---------------- */

function ProcessingStage({
  template,
  selectedDocs,
  simStatuses,
  progress,
  onCancel,
}: {
  template?: ExtractionTemplate;
  selectedDocs: DocumentMetadata[];
  simStatuses: Record<string, SimStatus>;
  progress: number;
  onCancel: () => void;
}) {
  const total = selectedDocs.length;
  const done = selectedDocs.filter(
    (d) => simStatuses[d.id] === "done" || simStatuses[d.id] === "failed",
  ).length;
  const processing = selectedDocs.filter(
    (d) => simStatuses[d.id] === "processing",
  ).length;
  const failed = selectedDocs.filter(
    (d) => simStatuses[d.id] === "failed",
  ).length;

  return (
    <div className="space-y-6">
      <SectionCard
        title="Batch extraction in progress"
        description={
          template
            ? `Applying "${template.name}" to ${total} document${
                total === 1 ? "" : "s"
              }`
            : "Applying template…"
        }
        actions={
          <Button variant="outline" size="sm" onClick={onCancel}>
            <XCircle className="size-4" />
            Cancel
          </Button>
        }
      >
        <div className="space-y-5">
          {/* progress bar */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="flex items-center gap-2 font-medium">
                <Loader2 className="size-4 animate-spin text-primary" />
                Processing documents…
              </span>
              <span className="tabular-nums text-muted-foreground">
                {Math.round(progress)}%
              </span>
            </div>
            <Progress value={progress} className="h-2.5" />
          </div>

          {/* counters */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <CounterCard
              icon={<CheckCircle2 className="size-4" />}
              label="Done"
              value={done}
              tone="emerald"
            />
            <CounterCard
              icon={<Loader2 className="size-4" />}
              label="Processing"
              value={processing}
              tone="amber"
            />
            <CounterCard
              icon={<XCircle className="size-4" />}
              label="Failed"
              value={failed}
              tone="rose"
            />
            <CounterCard
              icon={<FileStack className="size-4" />}
              label="Total"
              value={total}
              tone="slate"
            />
          </div>

          {/* per-document status list */}
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Document queue
            </p>
            <div className="max-h-[22rem] space-y-1.5 overflow-y-auto pr-1">
              {selectedDocs.map((d, i) => {
                const status = simStatuses[d.id] ?? "queued";
                return (
                  <motion.div
                    key={d.id}
                    custom={i}
                    variants={fade}
                    initial="hidden"
                    animate="show"
                  >
                    <div className="flex items-center gap-3 rounded-lg border border-border/60 bg-card p-2.5">
                      <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                        <FileStack className="size-4" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">
                          {d.name}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {docTypeLabel(d.type)} · {d.page_count} pages
                        </p>
                      </div>
                      <SimStatusPill status={status} />
                    </div>
                  </motion.div>
                );
              })}
            </div>
          </div>
        </div>
      </SectionCard>
    </div>
  );
}

function CounterCard({
  icon,
  label,
  value,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  tone: "emerald" | "amber" | "rose" | "slate";
}) {
  const toneClasses = {
    emerald:
      "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    amber: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    rose: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
    slate: "bg-muted text-muted-foreground",
  }[tone];
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border/60 bg-card p-3">
      <div
        className={`flex size-8 items-center justify-center rounded-md ${toneClasses}`}
      >
        {icon}
      </div>
      <div>
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-lg font-semibold tabular-nums">{value}</p>
      </div>
    </div>
  );
}

function SimStatusPill({ status }: { status: SimStatus }) {
  const map = {
    queued: {
      cls: "bg-slate-500/10 text-slate-600 dark:text-slate-300",
      icon: <Clock className="size-3" />,
      label: "Queued",
    },
    processing: {
      cls: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
      icon: <Loader2 className="size-3 animate-spin" />,
      label: "Processing",
    },
    done: {
      cls: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
      icon: <CheckCircle2 className="size-3" />,
      label: "Done",
    },
    failed: {
      cls: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
      icon: <XCircle className="size-3" />,
      label: "Failed",
    },
  }[status];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${map.cls}`}
    >
      {map.icon}
      {map.label}
    </span>
  );
}

/* ---------------- Stage 3: Results ---------------- */

function ResultsStage({
  result,
  template,
  onBack,
  onBenchmark,
  onExport,
  onInspect,
}: {
  result: BatchProcessingResult;
  template?: ExtractionTemplate;
  onBack: () => void;
  onBenchmark: () => void;
  onExport: () => void;
  onInspect: (item: BatchItemResult) => void;
}) {
  return (
    <>
      {/* Action bar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Layers className="size-4 text-primary" />
          <span className="font-medium text-foreground">
            {result.template_name}
          </span>
          <ChevronRight className="size-3.5" />
          <span>{result.total} documents processed</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={onBack}>
            <ArrowLeft className="size-4" />
            Back to configuration
          </Button>
          <Button variant="outline" onClick={onExport}>
            <Download className="size-4" />
            Export CSV
          </Button>
          <Button onClick={onBenchmark}>
            <Gauge className="size-4" />
            Run benchmark on these results
          </Button>
        </div>
      </div>

      {/* Summary stat cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard
          label="Total processed"
          value={result.total}
          hint={`Batch ${result.id.slice(0, 8)}`}
          icon={<FileStack className="size-5" />}
          tone="default"
        />
        <StatCard
          label="Succeeded"
          value={result.done}
          hint={pct(result.done / Math.max(result.total, 1), 0)}
          icon={<CheckCircle2 className="size-5" />}
          tone="success"
        />
        <StatCard
          label="Failed"
          value={result.failed}
          hint={
            result.failed === 0
              ? "No failures"
              : `${pct(result.failed / Math.max(result.total, 1), 0)} of batch`
          }
          icon={<XCircle className="size-5" />}
          tone={result.failed > 0 ? "danger" : "default"}
        />
        <StatCard
          label="Avg confidence"
          value={pct(result.average_confidence)}
          hint="Across all items"
          icon={<Target className="size-5" />}
          tone="primary"
        />
        <StatCard
          label="Avg latency"
          value={`${Math.round(result.average_latency_ms)}ms`}
          hint="Per document"
          icon={<Zap className="size-5" />}
          tone="default"
        />
      </div>

      {/* Results table */}
      <SectionCard
        title="Per-document results"
        description="Click Inspect to view extracted fields"
        noBodyPadding
      >
        <ScrollArea className="max-h-[40rem]">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead className="pl-4">Document</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead className="hidden md:table-cell">
                  Matched
                </TableHead>
                <TableHead className="hidden md:table-cell">
                  Mismatched
                </TableHead>
                <TableHead className="hidden lg:table-cell">
                  Missing
                </TableHead>
                <TableHead className="hidden sm:table-cell">Latency</TableHead>
                <TableHead className="w-10 pr-4" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.items.map((item) => (
                <TableRow key={item.document_id}>
                  <TableCell className="max-w-[260px] pl-4">
                    <div className="flex items-center gap-2.5">
                      <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                        <FileStack className="size-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">
                          {item.document_name}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {item.fields.length} fields extracted
                        </p>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <BatchStatusBadge status={item.status} />
                  </TableCell>
                  <TableCell>
                    {item.status === "failed" ? (
                      <span className="text-xs text-muted-foreground">—</span>
                    ) : (
                      <div className="flex items-center gap-2">
                        <span className="text-xs tabular-nums">
                          {pct(item.overall_confidence, 0)}
                        </span>
                        <ConfidenceBadge
                          level={confidenceLevelFor(item.overall_confidence)}
                        />
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="hidden md:table-cell text-xs tabular-nums text-emerald-600 dark:text-emerald-400">
                    {item.matched}
                  </TableCell>
                  <TableCell className="hidden md:table-cell text-xs tabular-nums text-amber-600 dark:text-amber-400">
                    {item.mismatched}
                  </TableCell>
                  <TableCell className="hidden lg:table-cell text-xs tabular-nums text-rose-600 dark:text-rose-400">
                    {item.missing}
                  </TableCell>
                  <TableCell className="hidden sm:table-cell text-xs text-muted-foreground tabular-nums">
                    {item.latency_ms}ms
                  </TableCell>
                  <TableCell className="pr-4">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onInspect(item)}
                        >
                          <Eye className="size-4" />
                          Inspect
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>View extracted fields</TooltipContent>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </ScrollArea>
      </SectionCard>

      {/* low-confidence / failure callouts */}
      {result.items.some(
        (it) =>
          it.status === "failed" ||
          (it.status === "done" && it.overall_confidence < 0.7),
      ) && (
        <Card className="flex items-start gap-3 border-amber-500/30 bg-amber-500/5 p-4">
          <AlertTriangle className="size-5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="text-sm">
            <p className="font-medium text-amber-700 dark:text-amber-300">
              Attention required
            </p>
            <p className="mt-0.5 text-muted-foreground">
              {result.items.filter(
                (it) =>
                  it.status === "failed" ||
                  (it.status === "done" && it.overall_confidence < 0.7),
              ).length}{" "}
              document(s) have low confidence or failed extraction. Review them
              in the table above or run a benchmark to tune the template.
            </p>
          </div>
        </Card>
      )}
    </>
  );
}

/* ---------------- Inspect Sheet ---------------- */

function InspectSheet({
  item,
  open,
  onOpenChange,
}: {
  item: BatchItemResult | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  // local editable copy of fields (display-only; no save)
  const [editedFields, setEditedFields] = React.useState<
    Record<string, string>
  >({});
  const [fieldNotes, setFieldNotes] = React.useState<Record<string, string>>(
    {},
  );

  React.useEffect(() => {
    setEditedFields({});
    setFieldNotes({});
  }, [item?.document_id]);

  if (!item) return null;

  const lowConfCount = item.fields.filter(
    (f) => f.confidence_level === "low",
  ).length;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full gap-0 p-0 sm:max-w-lg md:max-w-xl"
      >
        <SheetHeader className="border-b border-border/60 px-5 py-4">
          <SheetTitle className="text-base">Extracted fields</SheetTitle>
          <SheetDescription className="truncate">
            {item.document_name}
          </SheetDescription>
        </SheetHeader>

        {/* mini summary */}
        <div className="flex flex-wrap items-center gap-2 border-b border-border/60 px-5 py-3">
          <BatchStatusBadge status={item.status} />
          {item.status === "done" && (
            <ConfidenceBadge
              level={confidenceLevelFor(item.overall_confidence)}
            />
          )}
          <span className="text-xs text-muted-foreground tabular-nums">
            {pct(item.overall_confidence, 0)} overall
          </span>
          <Separator
            orientation="vertical"
            className="mx-1 h-4 w-px bg-border"
          />
          <span className="text-xs text-muted-foreground">
            {item.matched} matched
          </span>
          <span className="text-xs text-muted-foreground">
            {item.mismatched} mismatched
          </span>
          <span className="text-xs text-muted-foreground">
            {item.missing} missing
          </span>
          {lowConfCount > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/10 px-2 py-0.5 text-xs font-medium text-rose-600 dark:text-rose-400">
              <AlertTriangle className="size-3" />
              {lowConfCount} low
            </span>
          )}
        </div>

        <ScrollArea className="flex-1">
          <div className="space-y-3 p-5">
            {item.fields.length === 0 ? (
              <EmptyState
                icon={<FileStack className="size-5" />}
                title="No fields extracted"
                description="This document did not produce any extracted fields."
              />
            ) : (
              item.fields.map((field) => {
                const level = field.confidence_level;
                const isLow = level === "low";
                const editedValue =
                  editedFields[field.id] ?? formatFieldValue(field);
                return (
                  <div
                    key={field.id}
                    className={`rounded-lg border p-3.5 transition-colors ${
                      isLow
                        ? "border-rose-500/40 bg-rose-500/5"
                        : "border-border/60 bg-card"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <p className="text-sm font-medium">{field.label}</p>
                          {field.required && (
                            <span className="text-xs text-rose-500">*</span>
                          )}
                        </div>
                        <p className="font-mono text-xs text-muted-foreground">
                          {field.key}
                        </p>
                      </div>
                      <ConfidenceBadge level={level} />
                    </div>
                    <Input
                      value={editedValue}
                      onChange={(e) =>
                        setEditedFields((prev) => ({
                          ...prev,
                          [field.id]: e.target.value,
                        }))
                      }
                      className={`mt-2.5 h-9 ${
                        isLow ? "border-rose-500/40" : ""
                      }`}
                      aria-label={`Edit ${field.label}`}
                    />
                    {field.validation_message && !field.valid && (
                      <p className="mt-1.5 flex items-center gap-1 text-xs text-rose-600 dark:text-rose-400">
                        <AlertTriangle className="size-3" />
                        {field.validation_message}
                      </p>
                    )}
                    {field.notes && (
                      <p className="mt-1.5 text-xs text-muted-foreground">
                        {field.notes}
                      </p>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </ScrollArea>

        <div className="border-t border-border/60 px-5 py-3">
          <p className="text-xs text-muted-foreground">
            Edits are local to this preview and are not saved.
          </p>
        </div>
      </SheetContent>
    </Sheet>
  );
}
