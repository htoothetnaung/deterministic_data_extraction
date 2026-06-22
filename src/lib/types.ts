/**
 * TypeScript types mirroring the FastAPI Pydantic models.
 * Keep these in sync with backend/app/models/*.py.
 */

export type DocumentType =
  | "invoice" | "receipt" | "contract" | "report" | "form" | "id" | "other";

export type DocumentStatus =
  | "uploaded" | "queued" | "processing" | "ocr_done" | "reviewed" | "approved" | "failed";

export type DocumentSource = "upload" | "corporate_db";

export interface DocumentMetadata {
  id: string;
  name: string;
  type: DocumentType;
  source: DocumentSource;
  mime_type: string;
  size_bytes: number;
  page_count: number;
  status: DocumentStatus;
  tags: string[];
  collection: string | null;
  uploaded_at: string;
  processed_at: string | null;
  preview_url: string | null;
  confidence: number | null;
  notes: string | null;
}

export interface DocumentUploadAck {
  id: string;
  name: string;
  size_bytes: number;
  status: DocumentStatus;
  message: string;
}

export type BlockType =
  | "text" | "heading" | "table" | "key_value" | "image" | "signature";

export interface OcrBlock {
  id: string;
  page: number;
  type: BlockType;
  bbox: number[] | null;
  text: string;
  confidence: number;
  edited: boolean;
  data: Record<string, unknown> | null;
}

export interface OcrResult {
  id: string;
  document_id: string;
  engine: string;
  language: string;
  pages: number;
  blocks: OcrBlock[];
  overall_confidence: number;
  processed_at: string;
  edited: boolean;
  approved: boolean;
}

export type FieldType =
  | "text" | "number" | "date" | "currency" | "email"
  | "phone" | "select" | "multiselect" | "boolean" | "table" | "regex";

export interface EditableExtractionField {
  id: string;
  label: string;
  key: string;
  type: FieldType;
  value: unknown;
  raw_value: unknown;
  confidence: number;
  confidence_level: "high" | "medium" | "low";
  required: boolean;
  edited: boolean;
  valid: boolean;
  validation_message: string | null;
  options: string[] | null;
  bbox: number[] | null;
  notes: string | null;
}

export interface TemplateFieldDefinition {
  id: string;
  label: string;
  key: string;
  type: FieldType;
  example_value: string | null;
  validation_rule: string | null;
  required: boolean;
  notes: string | null;
  extraction_hint: string | null;
  default_value: string | null;
}

export interface ExtractionTemplate {
  id: string;
  name: string;
  description: string | null;
  document_type: DocumentType;
  fields: TemplateFieldDefinition[];
  ocr_method: string;
  chunking_strategy: string;
  max_pages: number;
  loop_condition: string | null;
  version: string;
  created_at: string;
  updated_at: string;
  success_rate: number | null;
  usage_count: number;
  source_document_id: string | null;
}

export interface TemplateCreate {
  name: string;
  description?: string | null;
  document_type: DocumentType;
  fields: TemplateFieldDefinition[];
  ocr_method: string;
  chunking_strategy: string;
  max_pages: number;
  loop_condition?: string | null;
  source_document_id?: string | null;
}

export type BenchmarkStatus = "pending" | "running" | "completed" | "failed";

export interface BenchmarkMetric {
  name: string;
  label: string;
  value: number;
  unit: "ratio" | "ms" | "count" | "percent";
  description: string | null;
  target: number | null;
}

export interface FieldMetric {
  field_key: string;
  label: string;
  accuracy: number;
  exact_match: number;
  missing_count: number;
  correction_rate: number;
  confidence: number;
}

export interface RunSummary {
  run_id: string;
  template_name: string;
  files_processed: number;
  status: BenchmarkStatus;
  date: string;
  overall_accuracy: number | null;
  latency_ms: number | null;
}

export interface BenchmarkRun {
  id: string;
  run_id: string;
  template_id: string;
  template_name: string;
  document_ids: string[];
  status: BenchmarkStatus;
  started_at: string;
  finished_at: string | null;
  metrics: BenchmarkMetric[];
  field_metrics: FieldMetric[];
  consistency_samples: number[];
  notes: string | null;
}

export interface BenchmarkRunCreate {
  template_id: string;
  document_ids: string[];
  repeat: number;
}

export type BatchItemStatus = "queued" | "processing" | "done" | "failed";

export interface BatchItemResult {
  document_id: string;
  document_name: string;
  status: BatchItemStatus;
  fields: EditableExtractionField[];
  overall_confidence: number;
  latency_ms: number;
  error: string | null;
  matched: number;
  mismatched: number;
  missing: number;
}

export interface BatchProcessingResult {
  id: string;
  template_id: string;
  template_name: string;
  started_at: string;
  finished_at: string | null;
  total: number;
  done: number;
  failed: number;
  items: BatchItemResult[];
  average_confidence: number;
  average_latency_ms: number;
}

export interface BatchApplyRequest {
  template_id: string;
  document_ids: string[];
}
