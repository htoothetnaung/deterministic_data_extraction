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

export type ParserStatus = "ok" | "skipped" | "failed";

export interface ParserInputInfo {
  id: string;
  name: string;
  input_type: "pdf" | "image" | string;
  size_bytes: number;
  path: string;
  page_count: number;
}

export interface ParserInfo {
  id: string;
  name: string;
  supported_input_types: string[];
  installed: boolean;
  notes: string | null;
}

export interface ParserRunRequest {
  input_id: string;
  parsers: string[];
  preview_chars: number;
}

export interface ParserArtifactPaths {
  output_md: string | null;
  structured_json: string | null;
  corrections_json: string | null;
}

export interface ParserRunResult {
  result_id: string;
  run_id: string;
  library: string;
  input_file: string;
  input_type: string;
  status: ParserStatus;
  seconds: number;
  pages: number;
  chars: number;
  tables: number;
  images: number;
  error: string | null;
  text_preview: string;
  structured_preview: Record<string, unknown>;
  artifact_paths: ParserArtifactPaths;
}

export interface ParserRunResponse {
  run_id: string;
  input: ParserInputInfo;
  results: ParserRunResult[];
  started_at: string;
  finished_at: string;
}

export interface ParserRunSummary {
  run_id: string;
  input: ParserInputInfo;
  parser_count: number;
  ok: number;
  skipped: number;
  failed: number;
  fastest_library: string | null;
  fastest_seconds: number | null;
  started_at: string;
  finished_at: string;
}

export interface ParserGroundTruthField {
  key: string;
  label: string;
  value: string;
}

export interface ParserGroundTruth {
  input_id: string;
  input_name: string;
  expected_terms: string[];
  expected_fields: ParserGroundTruthField[];
  notes: string;
  updated_at: string;
}

export interface ParserCorrection {
  corrected_text: string;
  notes: string;
  updated_at: string;
}

export interface ParserQualityCheck {
  key: string;
  label: string;
  expected: string;
  found: boolean;
  confidence: number;
  match_type: string;
}

export interface ParserResultDetail {
  run: ParserRunResponse;
  result: ParserRunResult;
  full_text: string;
  ground_truth: ParserGroundTruth;
  corrections: ParserCorrection;
  quality_checks: ParserQualityCheck[];
}

export type ExtractionFieldType =
  | "text"
  | "number"
  | "date"
  | "currency"
  | "email"
  | "phone"
  | "boolean"
  | "list"
  | "table"
  | "object";

export interface ExtractionSchemaField {
  id: string;
  key: string;
  label: string;
  type: ExtractionFieldType;
  description: string | null;
  required: boolean;
  children: ExtractionSchemaField[];
}

export interface ExtractionLabSchema {
  name: string;
  description: string | null;
  fields: ExtractionSchemaField[];
}

export interface ExtractionLabSchemaTemplate {
  id: string;
  label: string;
  filename: string;
  schema: ExtractionLabSchema;
}

export type ChunkingStrategy = "document" | "page" | "table_row" | "sliding_window" | "block";

export interface ExtractionRunRequest {
  input_id: string;
  output_schema: ExtractionLabSchema;
  natural_language_query?: string | null;
  parser_id: string;
  chunking_strategy: ChunkingStrategy;
  chunk_size: number;
  chunk_overlap: number;
  max_pages: number;
  max_candidates_per_field: number;
  preview_chars: number;
  evidence_mode: "cleaner" | "llm_vlm";
  extraction_tier: "cost_effective" | "agentic" | "agentic_plus";
}

export type MultiDocumentMode = "per_document" | "cross_document";

export interface MultiDocumentExtractionRunRequest extends ExtractionRunRequest {
  input_ids: string[];
  multi_document_mode: MultiDocumentMode;
}

export interface MultiDocumentExtractionRunResponse {
  mode: MultiDocumentMode;
  results: ExtractionRunResponse[];
}

export interface SchemaGenerationRequest {
  input_ids: string[];
  natural_language_query?: string | null;
  parser_id: string;
  multi_document_mode: MultiDocumentMode;
  chunking_strategy: ChunkingStrategy;
  chunk_size: number;
  chunk_overlap: number;
  max_pages: number;
  preview_chars: number;
}

export interface SchemaGenerationResponse {
  schema_definition: ExtractionLabSchema;
  warnings: string[];
}

export interface ExtractionEvidence {
  chunk_id: string;
  page: number;
  type: string;
  text_preview: string;
  bbox: Record<string, number> | null;
}

export interface ExtractionChunk {
  id: string;
  page: number;
  type: string;
  char_count: number;
  text_preview: string;
  bbox: Record<string, number> | null;
  confidence: number | null;
  risk: string;
  warnings: string[];
  source_url: string | null;
  columns: string[];
  rows: Record<string, string>[];
  strategy: string;
  table_index: number | null;
  row_index: number | null;
  header: string[] | null;
  token_count: number | null;
}

export interface ExtractionFieldResult {
  key: string;
  label: string;
  type: ExtractionFieldType;
  required: boolean;
  value: unknown;
  raw_value: unknown;
  confidence: number;
  valid: boolean;
  validation_message: string | null;
  evidence: ExtractionEvidence[];
}

export interface ExtractionValidationError {
  loc: string;
  msg: string;
  type: string;
}

export interface ExtractionRunStats {
  parser_seconds: number;
  total_seconds: number;
  pages: number;
  chunks: number;
  fields: number;
  candidates_scanned: number;
  chunking_strategy: string;
  chunk_tokens: number;
  cleaned_evidence_used: boolean;
  cleaned_evidence_items: number;
  llm_reconstruction_used: boolean;
  llm_reconstruction_items: number;
  null_fields_detected: number;
  null_retries: number;
  recovered_nulls: number;
  candidate_conflicts: number;
  critic_issues: number;
  consistency_score: number;
  agentic_used: boolean;
  adk_available: boolean;
  model_used: string | null;
}

export interface ExtractionRunResponse {
  run_id: string;
  input: ParserInputInfo;
  parser_id: string;
  parser_name: string;
  parser_run_id: string | null;
  parser_run_started_at: string | null;
  evidence_mode: "cleaner" | "llm_vlm";
  extraction_tier: "cost_effective" | "agentic" | "agentic_plus";
  schema_model_name: string;
  schema_definition: ExtractionLabSchema;
  natural_language_query: string | null;
  data: Record<string, unknown>;
  fields: ExtractionFieldResult[];
  chunks: ExtractionChunk[];
  validation_errors: ExtractionValidationError[];
  warnings: string[];
  generated_code: string;
  stats: ExtractionRunStats;
  started_at: string;
  finished_at: string;
}

export interface ExtractionReportResponse {
  report_markdown: string;
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
