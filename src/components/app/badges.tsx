"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import type {
  BatchItemStatus,
  BenchmarkStatus,
  DocumentStatus,
} from "@/lib/types";
import {
  benchStatusLabel,
  batchStatusLabel,
  docStatusLabel,
} from "@/lib/format";

type Tone = "emerald" | "amber" | "rose" | "slate" | "violet" | "teal";

const toneClasses: Record<Tone, string> = {
  emerald:
    "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 ring-emerald-500/20",
  amber: "bg-amber-500/10 text-amber-700 dark:text-amber-300 ring-amber-500/20",
  rose: "bg-rose-500/10 text-rose-700 dark:text-rose-300 ring-rose-500/20",
  slate:
    "bg-slate-500/10 text-slate-700 dark:text-slate-300 ring-slate-500/20",
  violet:
    "bg-violet-500/10 text-violet-700 dark:text-violet-300 ring-violet-500/20",
  teal: "bg-teal-500/10 text-teal-700 dark:text-teal-300 ring-teal-500/20",
};

export function Badge({
  tone = "slate",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset whitespace-nowrap",
        toneClasses[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: DocumentStatus }) {
  const map: Record<DocumentStatus, Tone> = {
    uploaded: "slate",
    queued: "slate",
    processing: "amber",
    ocr_done: "teal",
    reviewed: "violet",
    approved: "emerald",
    failed: "rose",
  };
  return <Badge tone={map[status]}>{docStatusLabel(status)}</Badge>;
}

export function BenchStatusBadge({ status }: { status: BenchmarkStatus }) {
  const map: Record<BenchmarkStatus, Tone> = {
    pending: "slate",
    running: "amber",
    completed: "emerald",
    failed: "rose",
  };
  return <Badge tone={map[status]}>{benchStatusLabel(status)}</Badge>;
}

export function BatchStatusBadge({ status }: { status: BatchItemStatus }) {
  const map: Record<BatchItemStatus, Tone> = {
    queued: "slate",
    processing: "amber",
    done: "emerald",
    failed: "rose",
  };
  return <Badge tone={map[status]}>{batchStatusLabel(status)}</Badge>;
}

export function ConfidenceBadge({
  level,
}: {
  level: "high" | "medium" | "low";
}) {
  const map = {
    high: "emerald",
    medium: "amber",
    low: "rose",
  } as const;
  const label = { high: "High", medium: "Medium", low: "Low" } as const;
  return (
    <Badge tone={map[level]}>
      <span className="size-1.5 rounded-full bg-current opacity-70" />
      {label[level]} confidence
    </Badge>
  );
}
