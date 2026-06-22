"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  FileStack,
  FileCode2,
  Gauge,
  ScanText,
  Upload,
  Plus,
  Play,
  ArrowRight,
  CheckCircle2,
  Clock,
  Layers,
  TrendingUp,
} from "lucide-react";

import { PageHeader } from "@/components/app/page-header";
import { StatCard } from "@/components/app/stat-card";
import { SectionCard, EmptyState } from "@/components/app/section";
import {
  StatusBadge,
  ConfidenceBadge,
  BenchStatusBadge,
} from "@/components/app/badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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

import { documentsApi, templatesApi, benchmarksApi } from "@/lib/api";
import { useNav } from "@/lib/store";
import {
  formatRelative,
  pct,
  docTypeLabel,
} from "@/lib/format";
import type { BenchmarkRun } from "@/lib/types";

const fade = {
  hidden: { opacity: 0, y: 8 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.04, duration: 0.3, ease: "easeOut" },
  }),
};

export function DashboardView() {
  const go = useNav((s) => s.go);
  const review = useNav((s) => s.review);
  const applyTemplate = useNav((s) => s.applyTemplate);
  const benchmarkTemplate = useNav((s) => s.benchmarkTemplate);

  const docsQ = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.list(),
  });
  const templatesQ = useQuery({
    queryKey: ["templates"],
    queryFn: () => templatesApi.list(),
  });
  const benchmarksQ = useQuery({
    queryKey: ["benchmarks"],
    queryFn: () => benchmarksApi.list(),
  });

  const docs = docsQ.data ?? [];
  const templates = templatesQ.data ?? [];
  const benchmarks = benchmarksQ.data ?? [];
  const latestRun: BenchmarkRun | undefined = benchmarks[0];

  const avgAccuracy = latestRun?.metrics.find(
    (m) => m.name === "field_level_accuracy",
  )?.value;
  const successRate = latestRun?.metrics.find(
    (m) => m.name === "template_success_rate",
  )?.value;

  const recentDocs = docs.slice(0, 5);

  return (
    <div className="mx-auto w-full max-w-7xl space-y-7">
      <PageHeader
        eyebrow="Overview"
        title="Extraction workspace"
        description="Benchmark, test, and prepare deterministic AI data extraction workflows from your corporate documents."
        icon={<Gauge className="size-5" />}
        actions={
          <>
            <Button variant="outline" onClick={() => go("documents")}>
              <Upload className="size-4" />
              Upload Documents
            </Button>
            <Button onClick={() => go("templates")}>
              <Plus className="size-4" />
              Create New Template
            </Button>
          </>
        }
      />

      {/* Quick action cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          {
            title: "Upload Documents",
            desc: "Drag & drop or pick from the corporate DB",
            icon: Upload,
            view: "documents" as const,
          },
          {
            title: "Review Extraction",
            desc: "Edit OCR output & correct fields",
            icon: ScanText,
            view: "ocr-review" as const,
          },
          {
            title: "Create Template",
            desc: "Define fields, rules & chunking",
            icon: FileCode2,
            view: "templates" as const,
          },
          {
            title: "Run Benchmark",
            desc: "Measure deterministic performance",
            icon: Play,
            view: "benchmarking" as const,
          },
        ].map((a, i) => {
          const Icon = a.icon;
          return (
            <motion.button
              key={a.title}
              custom={i}
              variants={fade}
              initial="hidden"
              animate="show"
              onClick={() => go(a.view)}
              className="group relative overflow-hidden rounded-xl border border-border/70 bg-card p-4 text-left transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
            >
              <div className="flex items-center justify-between">
                <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Icon className="size-4.5" />
                </div>
                <ArrowRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
              </div>
              <p className="mt-3 text-sm font-semibold">{a.title}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">{a.desc}</p>
            </motion.button>
          );
        })}
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {docsQ.isLoading || templatesQ.isLoading || benchmarksQ.isLoading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-xl" />
          ))
        ) : (
          <>
            <StatCard
              label="Documents"
              value={docs.length}
              hint={`${docs.filter((d) => d.source === "corporate_db").length} in corporate DB`}
              icon={<FileStack className="size-5" />}
              tone="primary"
              trend={{ value: "+3 this week", direction: "up", good: true }}
            />
            <StatCard
              label="Templates"
              value={templates.length}
              hint={`${templates.reduce((a, t) => a + t.usage_count, 0)} total runs`}
              icon={<FileCode2 className="size-5" />}
              tone="default"
            />
            <StatCard
              label="Avg Field Accuracy"
              value={pct(avgAccuracy)}
              hint="Latest benchmark run"
              icon={<TrendingUp className="size-5" />}
              tone="success"
              trend={{
                value: "+1.2% vs prev",
                direction: "up",
                good: true,
              }}
            />
            <StatCard
              label="Template Success Rate"
              value={pct(successRate)}
              hint={`${benchmarks.length} benchmark runs`}
              icon={<CheckCircle2 className="size-5" />}
              tone="success"
            />
          </>
        )}
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* Recent documents */}
        <SectionCard
          title="Recent documents"
          description="Recently uploaded & processed files"
          className="lg:col-span-2"
          actions={
            <Button variant="ghost" size="sm" onClick={() => go("documents")}>
              View all
              <ArrowRight className="size-3.5" />
            </Button>
          }
          noBodyPadding
        >
          {docsQ.isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : recentDocs.length === 0 ? (
            <div className="p-4">
              <EmptyState
                icon={<FileStack className="size-5" />}
                title="No documents yet"
                description="Upload documents or pick from the corporate database to get started."
                action={
                  <Button size="sm" onClick={() => go("documents")}>
                    <Upload className="size-4" />
                    Upload documents
                  </Button>
                }
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/40 hover:bg-muted/40">
                  <TableHead>Name</TableHead>
                  <TableHead className="hidden sm:table-cell">Type</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden md:table-cell">Confidence</TableHead>
                  <TableHead className="hidden lg:table-cell">Added</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {recentDocs.map((d) => (
                  <TableRow
                    key={d.id}
                    className="cursor-pointer"
                    onClick={() => review(d.id)}
                  >
                    <TableCell className="max-w-[220px] font-medium">
                      <div className="flex items-center gap-2.5">
                        <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                          <FileStack className="size-4" />
                        </div>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{d.name}</p>
                          <p className="truncate text-xs text-muted-foreground">
                            {d.page_count} pages · {docTypeLabel(d.type)}
                          </p>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="hidden sm:table-cell text-xs text-muted-foreground">
                      {docTypeLabel(d.type)}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={d.status} />
                    </TableCell>
                    <TableCell className="hidden md:table-cell">
                      {d.confidence !== null && d.confidence > 0 ? (
                        <span className="text-xs tabular-nums">
                          {pct(d.confidence, 0)}
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="hidden lg:table-cell text-xs text-muted-foreground">
                      {formatRelative(d.uploaded_at)}
                    </TableCell>
                    <TableCell>
                      <ArrowRight className="size-4 text-muted-foreground" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </SectionCard>

        {/* Latest benchmark summary */}
        <SectionCard
          title="Latest benchmark"
          description={latestRun?.run_id ?? "No runs yet"}
          actions={
            <Button
              variant="ghost"
              size="sm"
              onClick={() => go("benchmarking")}
            >
              Details
              <ArrowRight className="size-3.5" />
            </Button>
          }
        >
          {benchmarksQ.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : !latestRun ? (
            <EmptyState
              icon={<Gauge className="size-5" />}
              title="No benchmarks yet"
              description="Run a benchmark to measure extraction quality."
              action={
                <Button size="sm" onClick={() => go("benchmarking")}>
                  <Play className="size-4" />
                  Run benchmark
                </Button>
              }
            />
          ) : (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs text-muted-foreground">Template</p>
                  <p className="text-sm font-medium">{latestRun.template_name}</p>
                </div>
                <BenchStatusBadge status={latestRun.status} />
              </div>
              <div className="space-y-2.5">
                {latestRun.metrics
                  .filter((m) => m.unit === "ratio")
                  .slice(0, 4)
                  .map((m) => {
                    const good =
                      m.target === null
                        ? undefined
                        : m.value >= m.target;
                    return (
                      <div key={m.name} className="space-y-1">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted-foreground">{m.label}</span>
                          <span className="font-medium tabular-nums">
                            {pct(m.value)}
                          </span>
                        </div>
                        <Progress
                          value={m.value * 100}
                          className="h-1.5"
                        />
                      </div>
                    );
                  })}
              </div>
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => benchmarkTemplate(latestRun.template_id)}
              >
                <Play className="size-4" />
                Run new benchmark
              </Button>
            </div>
          )}
        </SectionCard>
      </div>

      {/* Templates row */}
      <SectionCard
        title="Extraction templates"
        description="Reusable field definitions & extraction rules"
        actions={
          <Button variant="ghost" size="sm" onClick={() => go("templates")}>
            View all
            <ArrowRight className="size-3.5" />
          </Button>
        }
        noBodyPadding
      >
        {templatesQ.isLoading ? (
          <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-32 w-full rounded-lg" />
            ))}
          </div>
        ) : (
          <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
            {templates.map((t, i) => (
              <motion.div
                key={t.id}
                custom={i}
                variants={fade}
                initial="hidden"
                animate="show"
              >
                <Card
                  className="group h-full cursor-pointer p-4 transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
                  onClick={() => applyTemplate(t.id)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <FileCode2 className="size-4.5" />
                    </div>
                    {t.success_rate !== null ? (
                      <ConfidenceBadge
                        level={
                          t.success_rate >= 0.9
                            ? "high"
                            : t.success_rate >= 0.75
                              ? "medium"
                              : "low"
                        }
                      />
                    ) : null}
                  </div>
                  <p className="mt-3 text-sm font-semibold leading-tight">
                    {t.name}
                  </p>
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                    {t.description}
                  </p>
                  <div className="mt-3 flex items-center justify-between border-t border-border/60 pt-3 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <Layers className="size-3.5" />
                      {t.fields.length} fields
                    </span>
                    <span className="flex items-center gap-1">
                      <Clock className="size-3.5" />
                      {t.usage_count} runs
                    </span>
                  </div>
                </Card>
              </motion.div>
            ))}
          </div>
        )}
      </SectionCard>
    </div>
  );
}
