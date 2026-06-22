"use client";

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { toast } from "sonner";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Area,
  AreaChart,
  ReferenceLine,
  Cell,
} from "recharts";
import {
  Gauge,
  Play,
  Target,
  Clock,
  CheckCircle2,
  RefreshCw,
  Download,
  Eye,
  BarChart3,
  Activity,
  Repeat,
  FileBarChart,
  AlertTriangle,
  Info,
} from "lucide-react";

import { PageHeader } from "@/components/app/page-header";
import { StatCard } from "@/components/app/stat-card";
import { SectionCard, EmptyState } from "@/components/app/section";
import { BenchStatusBadge, Badge } from "@/components/app/badges";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";

import { benchmarksApi, templatesApi, documentsApi } from "@/lib/api";
import { useNav } from "@/lib/store";
import { pct, formatDate, formatRelative } from "@/lib/format";
import type {
  BenchmarkRun,
  BenchmarkMetric,
  FieldMetric,
  RunSummary,
} from "@/lib/types";

const fade = {
  hidden: { opacity: 0, y: 8 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.05, duration: 0.3, ease: "easeOut" as const },
  }),
};

/* ----------------------------- helpers ----------------------------- */

function findMetric(
  run: BenchmarkRun | undefined,
  name: string,
): BenchmarkMetric | undefined {
  return run?.metrics.find((m) => m.name === name);
}

function formatMetricValue(m: BenchmarkMetric): string {
  if (m.unit === "ratio") return pct(m.value);
  if (m.unit === "percent") return pct(m.value / 100);
  if (m.unit === "ms") return `${m.value.toFixed(0)} ms`;
  if (m.unit === "count") return `${Math.round(m.value)}`;
  return `${m.value}`;
}

function formatTarget(m: BenchmarkMetric): string {
  if (m.target === null) return "—";
  if (m.unit === "ratio") return pct(m.target, 0);
  if (m.unit === "percent") return pct(m.target / 100, 0);
  if (m.unit === "ms") return `${m.target.toFixed(0)} ms`;
  if (m.unit === "count") return `${Math.round(m.target)}`;
  return `${m.target}`;
}

function accuracyTone(v: number): string {
  if (v >= 0.9) return "text-emerald-600 dark:text-emerald-400";
  if (v >= 0.75) return "text-amber-600 dark:text-amber-400";
  return "text-rose-600 dark:text-rose-400";
}

/** Lower-is-better metrics flip the success comparison. */
function isLowerBetter(m: BenchmarkMetric): boolean {
  return (
    m.name === "ocr_correction_rate" ||
    m.name === "missing_field_count" ||
    m.unit === "ms"
  );
}

function exportRunCsv(run: RunSummary) {
  const rows: (string | number)[][] = [
    [
      "run_id",
      "template",
      "files_processed",
      "status",
      "overall_accuracy",
      "latency_ms",
      "date",
    ],
    [
      run.run_id,
      run.template_name,
      run.files_processed,
      run.status,
      run.overall_accuracy ?? "",
      run.latency_ms ?? "",
      run.date,
    ],
  ];
  const csv = rows
    .map((r) =>
      r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(","),
    )
    .join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `benchmark-${run.run_id}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function tooltipValueFormatter(value: unknown): string {
  const v = Array.isArray(value) ? value[0] : value;
  return pct(Number(v));
}

/* ----------------------------- view ----------------------------- */

export function BenchmarkingView() {
  const qc = useQueryClient();
  const benchmarkTemplateId = useNav((s) => s.benchmarkTemplateId);

  const benchmarksQ = useQuery({
    queryKey: ["benchmarks"],
    queryFn: () => benchmarksApi.list(),
  });
  const runsQ = useQuery({
    queryKey: ["benchmark-runs"],
    queryFn: () => benchmarksApi.runs(),
  });

  const runs = benchmarksQ.data ?? [];

  // Selected run id; falls back to the latest run when unset.
  const [selectedRunId, setSelectedRunId] = React.useState<string | null>(null);
  const effectiveRunId = selectedRunId ?? runs[0]?.run_id ?? null;
  const selectedRun = React.useMemo(
    () => runs.find((r) => r.run_id === effectiveRunId) ?? runs[0],
    [runs, effectiveRunId],
  );

  /* ---------- New benchmark dialog ---------- */
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [formTemplateId, setFormTemplateId] = React.useState<string>("");
  const [formDocIds, setFormDocIds] = React.useState<string[]>([]);
  const [formRepeat, setFormRepeat] = React.useState<number>(1);

  const templatesQ = useQuery({
    queryKey: ["templates"],
    queryFn: () => templatesApi.list(),
    enabled: dialogOpen,
  });
  const documentsQ = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.list(),
    enabled: dialogOpen,
  });

  // Pre-fill template when dialog opens (from nav handoff or first template).
  React.useEffect(() => {
    if (!dialogOpen) return;
    if (benchmarkTemplateId) {
      setFormTemplateId((cur) => (cur && cur !== benchmarkTemplateId ? cur : benchmarkTemplateId));
    } else if (templatesQ.data && templatesQ.data.length > 0 && !formTemplateId) {
      setFormTemplateId(templatesQ.data[0].id);
    }
  }, [dialogOpen, benchmarkTemplateId, templatesQ.data, formTemplateId]);

  const createMut = useMutation({
    mutationFn: benchmarksApi.create,
    onSuccess: (run) => {
      toast.success("Benchmark started", { description: `Run ${run.run_id}` });
      setDialogOpen(false);
      qc.invalidateQueries({ queryKey: ["benchmarks"] });
      qc.invalidateQueries({ queryKey: ["benchmark-runs"] });
      setSelectedRunId(run.run_id);
      setFormDocIds([]);
      setFormRepeat(1);
    },
    onError: (e: unknown) => {
      toast.error("Failed to start benchmark", {
        description: e instanceof Error ? e.message : "Unknown error",
      });
    },
  });

  const templates = templatesQ.data ?? [];
  const documents = documentsQ.data ?? [];
  const allDocsSelected =
    documents.length > 0 && formDocIds.length === documents.length;

  const toggleDoc = (id: string) => {
    setFormDocIds((cur) =>
      cur.includes(id) ? cur.filter((d) => d !== id) : [...cur, id],
    );
  };
  const toggleAllDocs = () => {
    if (allDocsSelected) setFormDocIds([]);
    else setFormDocIds(documents.map((d) => d.id));
  };

  const canRun =
    !!formTemplateId &&
    formDocIds.length > 0 &&
    formRepeat >= 1 &&
    !createMut.isPending;

  const handleRun = () => {
    if (!canRun) return;
    createMut.mutate({
      template_id: formTemplateId,
      document_ids: formDocIds,
      repeat: formRepeat,
    });
  };

  /* ---------- Derived top metrics ---------- */
  const fieldAcc = findMetric(selectedRun, "field_level_accuracy");
  const exactMatch = findMetric(selectedRun, "exact_match_score");
  const ocrCorr = findMetric(selectedRun, "ocr_correction_rate");
  const latency = findMetric(selectedRun, "processing_latency");

  /* ---------- Chart data ---------- */
  const fieldChartData = React.useMemo<
    { label: string; accuracy: number }[]
  >(
    () =>
      (selectedRun?.field_metrics ?? []).map((f) => ({
        label: f.label,
        accuracy: f.accuracy,
      })),
    [selectedRun],
  );

  const consistencyData = React.useMemo<
    { run: string; accuracy: number }[]
  >(
    () =>
      (selectedRun?.consistency_samples ?? []).map((v, i) => ({
        run: `Run ${i + 1}`,
        accuracy: v,
      })),
    [selectedRun],
  );

  const fieldChartConfig = {
    accuracy: { label: "Accuracy", color: "var(--chart-1)" },
  } satisfies ChartConfig;

  const consistencyChartConfig = {
    accuracy: { label: "Accuracy", color: "var(--chart-2)" },
  } satisfies ChartConfig;

  /* ---------- Render ---------- */
  return (
    <div className="mx-auto w-full max-w-7xl space-y-6">
      <PageHeader
        eyebrow="Step 5 · Quality"
        title="Benchmarking"
        description="Measure deterministic extraction quality, consistency across repeated runs, and processing latency."
        icon={<Gauge className="size-5" />}
        actions={
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Button>
                <Play className="size-4" />
                Run New Benchmark
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-lg">
              <DialogHeader>
                <DialogTitle>Run a new benchmark</DialogTitle>
                <DialogDescription>
                  Pick a template and documents to measure extraction quality
                  and determinism.
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-4">
                {/* Template */}
                <div className="space-y-1.5">
                  <Label htmlFor="bench-template">Template</Label>
                  <Select
                    value={formTemplateId}
                    onValueChange={setFormTemplateId}
                  >
                    <SelectTrigger id="bench-template" className="w-full">
                      <SelectValue placeholder="Select a template" />
                    </SelectTrigger>
                    <SelectContent>
                      {templatesQ.isLoading ? (
                        <SelectItem value="__loading" disabled>
                          Loading…
                        </SelectItem>
                      ) : templates.length === 0 ? (
                        <SelectItem value="__empty" disabled>
                          No templates available
                        </SelectItem>
                      ) : (
                        templates.map((t) => (
                          <SelectItem key={t.id} value={t.id}>
                            {t.name}
                          </SelectItem>
                        ))
                      )}
                    </SelectContent>
                  </Select>
                </div>

                {/* Documents multi-select */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <Label>
                      Documents
                      <span className="ml-1 text-muted-foreground">
                        ({formDocIds.length} selected)
                      </span>
                    </Label>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={toggleAllDocs}
                      disabled={
                        documentsQ.isLoading || documents.length === 0
                      }
                    >
                      {allDocsSelected ? "Clear all" : "Select all"}
                    </Button>
                  </div>
                  <div className="rounded-md border border-border/60">
                    <ScrollArea className="h-56">
                      <div className="divide-y divide-border/40">
                        {documentsQ.isLoading ? (
                          <div className="space-y-2 p-3">
                            {Array.from({ length: 4 }).map((_, i) => (
                              <Skeleton key={i} className="h-6 w-full" />
                            ))}
                          </div>
                        ) : documents.length === 0 ? (
                          <div className="p-3 text-xs text-muted-foreground">
                            No documents available.
                          </div>
                        ) : (
                          documents.map((d) => {
                            const checked = formDocIds.includes(d.id);
                            return (
                              <label
                                key={d.id}
                                className="flex cursor-pointer items-center gap-3 px-3 py-2 text-sm hover:bg-muted/40"
                              >
                                <Checkbox
                                  checked={checked}
                                  onCheckedChange={() => toggleDoc(d.id)}
                                />
                                <span className="flex-1 truncate">
                                  {d.name}
                                </span>
                                <span className="text-xs text-muted-foreground tabular-nums">
                                  {d.page_count}p
                                </span>
                              </label>
                            );
                          })
                        )}
                      </div>
                    </ScrollArea>
                  </div>
                </div>

                {/* Repeat */}
                <div className="space-y-1.5">
                  <Label htmlFor="bench-repeat">Repeat runs</Label>
                  <Input
                    id="bench-repeat"
                    type="number"
                    min={1}
                    max={10}
                    value={formRepeat}
                    onChange={(e) =>
                      setFormRepeat(
                        Math.max(
                          1,
                          Math.min(10, Number(e.target.value) || 1),
                        ),
                      )
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    Repeats each document to measure consistency (determinism).
                  </p>
                </div>
              </div>

              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="outline">Cancel</Button>
                </DialogClose>
                <Button onClick={handleRun} disabled={!canRun}>
                  {createMut.isPending ? (
                    <>
                      <RefreshCw className="size-4 animate-spin" />
                      Starting…
                    </>
                  ) : (
                    <>
                      <Play className="size-4" />
                      Run benchmark
                    </>
                  )}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      {/* Loading state */}
      {benchmarksQ.isLoading ? (
        <div className="space-y-6">
          <Skeleton className="h-10 w-72" />
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-28 w-full rounded-xl" />
            ))}
          </div>
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <Skeleton className="h-[340px] w-full rounded-xl" />
            <Skeleton className="h-[340px] w-full rounded-xl" />
          </div>
        </div>
      ) : runs.length === 0 ? (
        <SectionCard>
          <EmptyState
            icon={<Gauge className="size-5" />}
            title="No benchmark runs yet"
            description="Run your first benchmark to measure extraction accuracy, consistency, and latency."
            action={
              <Button onClick={() => setDialogOpen(true)}>
                <Play className="size-4" />
                Run benchmark
              </Button>
            }
          />
        </SectionCard>
      ) : !selectedRun ? (
        <SectionCard>
          <EmptyState
            icon={<AlertTriangle className="size-5" />}
            title="Select a run"
            description="Choose a benchmark run from the dropdown to view its metrics."
          />
        </SectionCard>
      ) : (
        <motion.div
          custom={0}
          variants={fade}
          initial="hidden"
          animate="show"
          className="space-y-6"
        >
          {/* Run selector */}
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Viewing run
              </Label>
              <Select
                value={effectiveRunId ?? undefined}
                onValueChange={setSelectedRunId}
              >
                <SelectTrigger className="w-full sm:w-[400px]">
                  <SelectValue placeholder="Select a run" />
                </SelectTrigger>
                <SelectContent>
                  {runs.map((r) => (
                    <SelectItem key={r.run_id} value={r.run_id}>
                      <span className="font-mono text-xs">{r.run_id}</span>
                      <span className="text-muted-foreground">
                        {" · "}
                        {r.template_name}
                        {" · "}
                        {formatRelative(r.started_at)}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Badge tone="emerald">
                <CheckCircle2 className="size-3" />
                {selectedRun.template_name}
              </Badge>
              <Separator orientation="vertical" className="h-4" />
              <span className="tabular-nums">
                {selectedRun.document_ids.length} docs
              </span>
              <Separator orientation="vertical" className="h-4" />
              <span>{formatDate(selectedRun.started_at)}</span>
            </div>
          </div>

          {/* Top metric summary cards */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              label="Field-level Accuracy"
              value={pct(fieldAcc?.value)}
              hint={
                fieldAcc?.target !== null && fieldAcc?.target !== undefined
                  ? `target ${pct(fieldAcc.target, 0)}`
                  : "no target set"
              }
              icon={<Target className="size-5" />}
              tone="success"
              trend={
                fieldAcc && fieldAcc.target !== null
                  ? {
                      value:
                        fieldAcc.value >= fieldAcc.target
                          ? "meets target"
                          : "below target",
                      direction: fieldAcc.value >= fieldAcc.target ? "up" : "down",
                      good: fieldAcc.value >= fieldAcc.target,
                    }
                  : undefined
              }
            />
            <StatCard
              label="Exact Match Score"
              value={pct(exactMatch?.value)}
              hint={
                exactMatch?.target !== null && exactMatch?.target !== undefined
                  ? `target ${pct(exactMatch.target, 0)}`
                  : "no target set"
              }
              icon={<CheckCircle2 className="size-5" />}
              tone="primary"
              trend={
                exactMatch && exactMatch.target !== null
                  ? {
                      value:
                        exactMatch.value >= exactMatch.target
                          ? "meets target"
                          : "below target",
                      direction:
                        exactMatch.value >= exactMatch.target ? "up" : "down",
                      good: exactMatch.value >= exactMatch.target,
                    }
                  : undefined
              }
            />
            <StatCard
              label="OCR Correction Rate"
              value={pct(ocrCorr?.value)}
              hint={
                ocrCorr?.target !== null && ocrCorr?.target !== undefined
                  ? `target ≤ ${pct(ocrCorr.target, 0)}`
                  : "no target set"
              }
              icon={<Activity className="size-5" />}
              tone="warning"
              trend={
                ocrCorr && ocrCorr.target !== null
                  ? {
                      value:
                        ocrCorr.value <= ocrCorr.target
                          ? "within target"
                          : "over target",
                      direction:
                        ocrCorr.value <= ocrCorr.target ? "down" : "up",
                      good: ocrCorr.value <= ocrCorr.target,
                    }
                  : undefined
              }
            />
            <StatCard
              label="Processing Latency"
              value={latency ? `${latency.value.toFixed(0)} ms` : "—"}
              hint={
                latency?.target !== null && latency?.target !== undefined
                  ? `avg per document · target ≤ ${latency.target.toFixed(0)} ms`
                  : "avg per document"
              }
              icon={<Clock className="size-5" />}
              tone="default"
              trend={
                latency && latency.target !== null
                  ? {
                      value:
                        latency.value <= latency.target
                          ? "within budget"
                          : "over budget",
                      direction:
                        latency.value <= latency.target ? "down" : "up",
                      good: latency.value <= latency.target,
                    }
                  : undefined
              }
            />
          </div>

          {/* Charts row */}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <SectionCard
              title="Field-level accuracy by field"
              description="Per-field accuracy against the 95% target"
              noBodyPadding
              bodyClassName="p-4"
            >
              {fieldChartData.length === 0 ? (
                <EmptyState
                  icon={<BarChart3 className="size-5" />}
                  title="No field metrics"
                  description="This run has no field-level metrics to display."
                />
              ) : (
                <ChartContainer
                  config={fieldChartConfig}
                  className="h-[280px] w-full"
                >
                  <BarChart
                    data={fieldChartData}
                    layout="vertical"
                    margin={{ left: 8, right: 16, top: 8, bottom: 8 }}
                  >
                    <CartesianGrid horizontal={false} />
                    <XAxis
                      type="number"
                      domain={[0, 1]}
                      tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
                      fontSize={11}
                    />
                    <YAxis
                      type="category"
                      dataKey="label"
                      width={120}
                      fontSize={11}
                    />
                    <ChartTooltip
                      content={
                        <ChartTooltipContent
                          formatter={(value) => (
                            <>
                              <span className="text-muted-foreground">
                                Accuracy
                              </span>
                              <span className="ml-auto font-mono font-medium tabular-nums">
                                {tooltipValueFormatter(value)}
                              </span>
                            </>
                          )}
                        />
                      }
                    />
                    <ReferenceLine
                      x={0.95}
                      stroke="var(--chart-3)"
                      strokeDasharray="4 4"
                    />
                    <Bar
                      dataKey="accuracy"
                      radius={4}
                      fill="var(--color-accuracy)"
                    >
                      {fieldChartData.map((entry, i) => (
                        <Cell
                          key={`cell-${i}`}
                          fill={
                            entry.accuracy >= 0.9
                              ? "var(--chart-1)"
                              : entry.accuracy >= 0.75
                                ? "var(--chart-3)"
                                : "var(--chart-4)"
                          }
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ChartContainer>
              )}
            </SectionCard>

            <SectionCard
              title="Consistency across repeated runs"
              description="Determinism — accuracy variance across repeats"
              noBodyPadding
              bodyClassName="p-4"
            >
              {consistencyData.length === 0 ? (
                <EmptyState
                  icon={<Repeat className="size-5" />}
                  title="No consistency samples"
                  description="Run the benchmark with repeat > 1 to measure determinism."
                />
              ) : (
                <ChartContainer
                  config={consistencyChartConfig}
                  className="h-[280px] w-full"
                >
                  <AreaChart
                    data={consistencyData}
                    margin={{ left: 8, right: 16, top: 8, bottom: 8 }}
                  >
                    <defs>
                      <linearGradient
                        id="consistencyFill"
                        x1="0"
                        y1="0"
                        x2="0"
                        y2="1"
                      >
                        <stop
                          offset="0%"
                          stopColor="var(--chart-2)"
                          stopOpacity={0.3}
                        />
                        <stop
                          offset="100%"
                          stopColor="var(--chart-2)"
                          stopOpacity={0}
                        />
                      </linearGradient>
                    </defs>
                    <CartesianGrid />
                    <XAxis dataKey="run" fontSize={11} />
                    <YAxis
                      domain={[0.8, 1]}
                      tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
                      fontSize={11}
                    />
                    <ChartTooltip
                      content={
                        <ChartTooltipContent
                          formatter={(value) => (
                            <>
                              <span className="text-muted-foreground">
                                Accuracy
                              </span>
                              <span className="ml-auto font-mono font-medium tabular-nums">
                                {tooltipValueFormatter(value)}
                              </span>
                            </>
                          )}
                        />
                      }
                    />
                    <Area
                      type="monotone"
                      dataKey="accuracy"
                      stroke="var(--color-accuracy)"
                      strokeWidth={2}
                      fill="url(#consistencyFill)"
                      dot={{ r: 3, fill: "var(--color-accuracy)" }}
                    />
                  </AreaChart>
                </ChartContainer>
              )}
            </SectionCard>
          </div>

          {/* Metrics breakdown table */}
          <SectionCard
            title="Metrics breakdown"
            description="All quality, consistency, and latency metrics for the selected run"
            noBodyPadding
          >
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/40 hover:bg-muted/40">
                  <TableHead>Metric</TableHead>
                  <TableHead className="text-right">Value</TableHead>
                  <TableHead className="text-right">Target</TableHead>
                  <TableHead className="w-[200px]">vs target</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {selectedRun.metrics.map((m) => {
                  const lower = isLowerBetter(m);
                  const meets =
                    m.target === null
                      ? null
                      : lower
                        ? m.value <= m.target
                        : m.value >= m.target;
                  const progressVal =
                    m.unit === "ratio"
                      ? m.target !== null && m.target > 0
                        ? Math.min(100, (m.value / m.target) * 100)
                        : m.value * 100
                      : 0;
                  return (
                    <TableRow key={m.name}>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <span className="text-sm font-medium">{m.label}</span>
                          {m.description ? (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <button
                                  type="button"
                                  className="text-muted-foreground/70 transition-colors hover:text-foreground"
                                  aria-label={`Description for ${m.label}`}
                                >
                                  <Info className="size-3.5" />
                                </button>
                              </TooltipTrigger>
                              <TooltipContent className="max-w-xs">
                                {m.description}
                              </TooltipContent>
                            </Tooltip>
                          ) : null}
                        </div>
                        <p className="font-mono text-xs text-muted-foreground">
                          {m.name}
                        </p>
                      </TableCell>
                      <TableCell className="text-right font-medium tabular-nums">
                        {formatMetricValue(m)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-muted-foreground">
                        {formatTarget(m)}
                      </TableCell>
                      <TableCell>
                        {m.unit === "ratio" ? (
                          <div className="flex items-center gap-2">
                            <Progress value={progressVal} className="h-2" />
                            {meets !== null && (
                              <span
                                className={
                                  meets
                                    ? "text-emerald-600 dark:text-emerald-400"
                                    : "text-rose-600 dark:text-rose-400"
                                }
                                aria-label={
                                  meets ? "Meets target" : "Below target"
                                }
                              >
                                {meets ? (
                                  <CheckCircle2 className="size-3.5" />
                                ) : (
                                  <AlertTriangle className="size-3.5" />
                                )}
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            {meets === null
                              ? "—"
                              : meets
                                ? lower
                                  ? "within budget"
                                  : "meets target"
                                : lower
                                  ? "over budget"
                                  : "below target"}
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </SectionCard>

          {/* Field-level details table */}
          <SectionCard
            title="Field-level details"
            description="Per-field accuracy, exact match, corrections, and confidence"
            noBodyPadding
          >
            {selectedRun.field_metrics.length === 0 ? (
              <div className="p-4">
                <EmptyState
                  icon={<FileBarChart className="size-5" />}
                  title="No field-level details"
                  description="This run has no per-field metrics available."
                />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableHead>Field</TableHead>
                    <TableHead className="w-[220px]">Accuracy</TableHead>
                    <TableHead className="text-right">Exact Match</TableHead>
                    <TableHead className="text-right">Missing</TableHead>
                    <TableHead className="text-right">
                      Correction Rate
                    </TableHead>
                    <TableHead className="text-right">Confidence</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {selectedRun.field_metrics.map(
                    (f: FieldMetric) => (
                      <TableRow key={f.field_key}>
                        <TableCell>
                          <p className="text-sm font-medium">{f.label}</p>
                          <p className="font-mono text-xs text-muted-foreground">
                            {f.field_key}
                          </p>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <Progress
                              value={f.accuracy * 100}
                              className="h-2"
                            />
                            <span
                              className={`text-xs font-medium tabular-nums ${accuracyTone(f.accuracy)}`}
                            >
                              {pct(f.accuracy, 1)}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {pct(f.exact_match)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {f.missing_count}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {pct(f.correction_rate)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {pct(f.confidence)}
                        </TableCell>
                      </TableRow>
                    ),
                  )}
                </TableBody>
              </Table>
            )}
          </SectionCard>

          {/* Extraction history table */}
          <SectionCard
            title="Extraction history"
            description="All benchmark runs"
            noBodyPadding
          >
            {runsQ.isLoading ? (
              <div className="space-y-2 p-4">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : (runsQ.data ?? []).length === 0 ? (
              <div className="p-4">
                <EmptyState
                  icon={<Clock className="size-5" />}
                  title="No run history"
                  description="Runs will appear here after you run your first benchmark."
                />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableHead>Run ID</TableHead>
                    <TableHead className="hidden md:table-cell">
                      Template
                    </TableHead>
                    <TableHead className="text-right">Files</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Accuracy</TableHead>
                    <TableHead className="hidden text-right sm:table-cell">
                      Latency
                    </TableHead>
                    <TableHead className="hidden lg:table-cell">Date</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(runsQ.data ?? []).map((r) => {
                    const isSelected = r.run_id === selectedRun?.run_id;
                    return (
                      <TableRow
                        key={r.run_id}
                        className={
                          isSelected
                            ? "bg-primary/5 cursor-pointer"
                            : "cursor-pointer"
                        }
                        onClick={() => setSelectedRunId(r.run_id)}
                      >
                        <TableCell className="font-mono text-xs">
                          {r.run_id}
                        </TableCell>
                        <TableCell className="hidden text-sm md:table-cell">
                          {r.template_name}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {r.files_processed}
                        </TableCell>
                        <TableCell>
                          <BenchStatusBadge status={r.status} />
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {r.overall_accuracy !== null
                            ? pct(r.overall_accuracy)
                            : "—"}
                        </TableCell>
                        <TableCell className="hidden text-right tabular-nums sm:table-cell">
                          {r.latency_ms !== null
                            ? `${r.latency_ms.toFixed(0)} ms`
                            : "—"}
                        </TableCell>
                        <TableCell className="hidden text-xs text-muted-foreground lg:table-cell">
                          {formatRelative(r.date)}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedRunId(r.run_id);
                              }}
                            >
                              <Eye className="size-3.5" />
                              View
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={(e) => {
                                e.stopPropagation();
                                exportRunCsv(r);
                              }}
                            >
                              <Download className="size-3.5" />
                              Export
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </SectionCard>
        </motion.div>
      )}
    </div>
  );
}
