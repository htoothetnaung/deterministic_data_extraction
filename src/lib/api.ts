import type {
  BatchApplyRequest,
  BatchProcessingResult,
  BenchmarkRun,
  BenchmarkRunCreate,
  DocumentMetadata,
  DocumentUploadAck,
  EditableExtractionField,
  ExtractionRunRequest,
  ExtractionRunResponse,
  ExtractionLabSchemaTemplate,
  ExtractionLabSchema,
  ExtractionReportResponse,
  MultiDocumentExtractionRunRequest,
  MultiDocumentExtractionRunResponse,
  JobHistoryItem,
  ExtractionTemplate,
  OcrResult,
  ParserInfo,
  ParserInputInfo,
  ParserCorrection,
  ParserGroundTruth,
  ParserResultDetail,
  ParserRunRequest,
  ParserRunResponse,
  ParserRunSummary,
  RunSummary,
  SchemaGenerationRequest,
  SchemaGenerationResponse,
  TemplateCreate,
} from "./types";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

function backendUrl(path: string): string {
  return `${BACKEND_URL}${path}`;
}

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND_URL}${path}`, {
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

/* ---------------- Parser Benchmarks ---------------- */
export const parserBenchmarksApi = {
  assetUrl: (path: string) => {
    if (/^https?:\/\//i.test(path) || path.startsWith("data:")) return path;
    return backendUrl(path.startsWith("/") ? path : `/${path}`);
  },
  inputs: () => request<ParserInputInfo[]>(`/api/parser-benchmarks/inputs`),
  previewUrl: (inputId: string) =>
    backendUrl(`/api/parser-benchmarks/preview/${encodeURIComponent(inputId)}`),
  pageImageUrl: (inputId: string, page: number, zoom = 1.4) =>
    backendUrl(
      `/api/parser-benchmarks/preview-page/${encodeURIComponent(inputId)}?page=${page}&zoom=${zoom}`,
    ),
  previewText: (inputId: string) =>
    fetch(backendUrl(`/api/parser-benchmarks/preview-text/${encodeURIComponent(inputId)}`)).then((res) => {
      if (!res.ok) throw new ApiError(res.status, res.statusText);
      return res.text();
    }),
  parsers: () => request<ParserInfo[]>(`/api/parser-benchmarks/parsers`),
  runs: () => request<ParserRunSummary[]>(`/api/parser-benchmarks/runs`),
  getRun: (runId: string) => request<ParserRunResponse>(`/api/parser-benchmarks/runs/${encodeURIComponent(runId)}`),
  getResult: (runId: string, library: string) =>
    request<ParserResultDetail>(
      `/api/parser-benchmarks/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(library)}`,
    ),
  getGroundTruth: (inputId: string) =>
    request<ParserGroundTruth>(`/api/parser-benchmarks/ground-truth/${encodeURIComponent(inputId)}`),
  saveGroundTruth: (inputId: string, body: ParserGroundTruth) =>
    request<ParserGroundTruth>(`/api/parser-benchmarks/ground-truth/${encodeURIComponent(inputId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  saveCorrections: (runId: string, library: string, body: ParserCorrection) =>
    request<ParserCorrection>(
      `/api/parser-benchmarks/runs/${encodeURIComponent(runId)}/results/${encodeURIComponent(library)}/corrections`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  run: (body: ParserRunRequest) =>
    request<ParserRunResponse>(`/api/parser-benchmarks/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};

/* ---------------- Extraction Lab ---------------- */
export const extractionLabApi = {
  history: () => request<JobHistoryItem[]>(`/api/extraction-lab/history`),
  getJobResult: (runId: string) => request<ExtractionRunResponse>(`/api/extraction-lab/results/job/${encodeURIComponent(runId)}`),
  inputs: () => request<ParserInputInfo[]>(`/api/extraction-lab/inputs`),
  parsers: () => request<ParserInfo[]>(`/api/extraction-lab/parsers`),
  schemas: () => request<ExtractionLabSchemaTemplate[]>(`/api/extraction-lab/schemas`),
  saveSchema: (schema: ExtractionLabSchema) =>
    request<ExtractionLabSchemaTemplate>(`/api/extraction-lab/schemas`, {
      method: "POST",
      body: JSON.stringify(schema),
      headers: { "Content-Type": "application/json" },
    }),
  deleteSchema: (schemaId: string) =>
    request<{ ok: boolean; deleted_id: string }>(`/api/extraction-lab/schemas/${encodeURIComponent(schemaId)}`, {
      method: "DELETE",
    }),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ParserInputInfo>(`/api/extraction-lab/upload`, { method: "POST", body: form });
  },
  uploadMany: (files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return request<ParserInputInfo[]>(`/api/extraction-lab/upload-multiple`, { method: "POST", body: form });
  },
  deleteInput: (inputId: string) =>
    request<{ ok: boolean; deleted_id: string }>(`/api/extraction-lab/inputs/${encodeURIComponent(inputId)}`, {
      method: "DELETE",
    }),
  run: (body: ExtractionRunRequest) =>
    request<ExtractionRunResponse>(`/api/extraction-lab/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  runMulti: (body: MultiDocumentExtractionRunRequest) =>
    request<MultiDocumentExtractionRunResponse>(`/api/extraction-lab/run-multi`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  generateSchema: (body: SchemaGenerationRequest) =>
    request<SchemaGenerationResponse>(`/api/extraction-lab/generate-schema`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  report: (body: { result: ExtractionRunResponse }) =>
    request<ExtractionReportResponse>(`/api/extraction-lab/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getResults: (inputId: string) =>
    request<ExtractionRunResponse[]>(`/api/extraction-lab/results/${encodeURIComponent(inputId)}`),
  deleteResult: (runId: string) =>
    request<{ ok: boolean; deleted_run_id: string }>(`/api/extraction-lab/results/${encodeURIComponent(runId)}`, {
      method: "DELETE",
    }),
};
