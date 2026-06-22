/**
 * Typed API client for the ExtractIQ FastAPI backend.
 *
 * The backend runs on port 8000. The gateway forwards requests using the
 * `XTransformPort` query parameter, so every call appends `?XTransformPort=8000`.
 *
 * All requests use relative paths so they work behind the Caddy gateway.
 */
import type {
  BatchApplyRequest,
  BatchProcessingResult,
  BenchmarkRun,
  BenchmarkRunCreate,
  DocumentMetadata,
  DocumentUploadAck,
  EditableExtractionField,
  ExtractionTemplate,
  OcrResult,
  RunSummary,
  TemplateCreate,
} from "./types";

const BACKEND_PORT = 8000;

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

function withPort(path: string): string {
  const sep = path.includes("?") ? "&" : "?";
  return path.includes("XTransformPort") ? path : `${path}${sep}XTransformPort=${BACKEND_PORT}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(withPort(path), {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/* ---------------- Documents ---------------- */
export const documentsApi = {
  list: (params?: { source?: string; type?: string; collection?: string; q?: string }) => {
    const qs = new URLSearchParams();
    if (params?.source) qs.set("source", params.source);
    if (params?.type) qs.set("type", params.type);
    if (params?.collection) qs.set("collection", params.collection);
    if (params?.q) qs.set("q", params.q);
    const q = qs.toString();
    return request<DocumentMetadata[]>(`/api/documents${q ? `?${q}` : ""}`);
  },
  get: (id: string) => request<DocumentMetadata>(`/api/documents/${id}`),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentUploadAck>(`/api/documents/upload`, { method: "POST", body: form });
  },
  process: (id: string) => request<DocumentMetadata>(`/api/documents/${id}/process`, { method: "POST" }),
  remove: (id: string) => request<{ ok: boolean; id: string }>(`/api/documents/${id}`, { method: "DELETE" }),
};

/* ---------------- OCR ---------------- */
export const ocrApi = {
  get: (docId: string) => request<OcrResult>(`/api/ocr/${docId}`),
  update: (docId: string, body: { blocks?: OcrResult["blocks"]; approved?: boolean; engine?: string }) =>
    request<OcrResult>(`/api/ocr/${docId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  reset: (docId: string) => request<OcrResult>(`/api/ocr/${docId}/reset`, { method: "POST" }),
  fields: (docId: string) => request<EditableExtractionField[]>(`/api/ocr/${docId}/fields`),
};

/* ---------------- Templates ---------------- */
export const templatesApi = {
  list: () => request<ExtractionTemplate[]>(`/api/templates`),
  get: (id: string) => request<ExtractionTemplate>(`/api/templates/${id}`),
  create: (body: TemplateCreate) =>
    request<ExtractionTemplate>(`/api/templates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  update: (id: string, body: Partial<ExtractionTemplate>) =>
    request<ExtractionTemplate>(`/api/templates/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  remove: (id: string) => request<{ ok: boolean; id: string }>(`/api/templates/${id}`, { method: "DELETE" }),
};

/* ---------------- Batch ---------------- */
export const batchApi = {
  apply: (body: BatchApplyRequest) =>
    request<BatchProcessingResult>(`/api/batch/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  list: () => request<BatchProcessingResult[]>(`/api/batch`),
  get: (id: string) => request<BatchProcessingResult>(`/api/batch/${id}`),
};

/* ---------------- Benchmarks ---------------- */
export const benchmarksApi = {
  list: () => request<BenchmarkRun[]>(`/api/benchmarks`),
  runs: () => request<RunSummary[]>(`/api/benchmarks/runs`),
  create: (body: BenchmarkRunCreate) =>
    request<BenchmarkRun>(`/api/benchmarks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  get: (runId: string) => request<BenchmarkRun>(`/api/benchmarks/${runId}`),
};
