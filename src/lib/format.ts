/** Formatting + display helpers. */
import type {
  BatchItemStatus,
  BenchmarkStatus,
  DocumentStatus,
  DocumentType,
} from "./types";

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "—";
  const diff = Date.now() - d;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  return formatDate(iso);
}

export function pct(ratio: number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function confidenceLevel(score: number): "high" | "medium" | "low" {
  if (score >= 0.9) return "high";
  if (score >= 0.7) return "medium";
  return "low";
}

const DOC_TYPE_LABEL: Record<DocumentType, string> = {
  invoice: "Invoice",
  receipt: "Receipt",
  contract: "Contract",
  report: "Report",
  form: "Form",
  id: "ID",
  other: "Other",
};

const DOC_STATUS_LABEL: Record<DocumentStatus, string> = {
  uploaded: "Uploaded",
  queued: "Queued",
  processing: "Processing",
  ocr_done: "OCR Done",
  reviewed: "Reviewed",
  approved: "Approved",
  failed: "Failed",
};

const BENCH_STATUS_LABEL: Record<BenchmarkStatus, string> = {
  pending: "Pending",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
};

const BATCH_STATUS_LABEL: Record<BatchItemStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  done: "Done",
  failed: "Failed",
};

export function docTypeLabel(t: DocumentType): string {
  return DOC_TYPE_LABEL[t] ?? t;
}
export function docStatusLabel(s: DocumentStatus): string {
  return DOC_STATUS_LABEL[s] ?? s;
}
export function benchStatusLabel(s: BenchmarkStatus): string {
  return BENCH_STATUS_LABEL[s] ?? s;
}
export function batchStatusLabel(s: BatchItemStatus): string {
  return BATCH_STATUS_LABEL[s] ?? s;
}
