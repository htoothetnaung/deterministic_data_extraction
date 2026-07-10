# Atenxion — Backend API & Database Specification

> **Service**: FastAPI (Python 3.11+)  
> **Base URL**: `http://localhost:8000/api`  
> **Schema**: OpenAPI auto-generated at `/docs` (Swagger) and `/redoc`  
> **Auth**: None currently; `user_id` defaults to `"local"` throughout

---

## 1. FastAPI Built-in Endpoints

Served directly by the FastAPI framework — no `/api` prefix.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Root — service info + link to `/docs` |
| `GET` | `/health` | Liveness check |
| `GET` | `/docs` | Swagger UI (interactive API console) |
| `GET` | `/redoc` | ReDoc API documentation |
| `GET` | `/openapi.json` | OpenAPI 3.0 JSON schema |

**`GET /`** response:
```json
{
  "service": "Atenxion",
  "version": "0.1.0",
  "status": "ok",
  "docs": "/docs"
}
```

All business endpoints below are prefixed with `/api`.  
Example: `GET /api/cases` → `http://localhost:8000/api/cases`

---

## 2. Database Schema (PostgreSQL + pgvector)

### 2.1 Entity-Relationship Overview

```
cases ──1:N── documents ──1:N── pages
  │                            │
  │                            └──1:N── evidence_items ──1:1── evidence_embeddings
  │
  └──1:N── extraction_jobs ──1:N── field_results ──1:N── field_candidates
                                                    └──1:N── field_attempts

documents ──1:N── document_jobs  (internal queue)
```

### 2.2 Table: `cases`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `case_id` | `VARCHAR(50)` | PK | UUID (12-char hex) |
| `user_id` | `VARCHAR(100)` | NOT NULL, default `'local'` | Owner |
| `title` | `VARCHAR(500)` | NOT NULL | Case name |
| `status` | `VARCHAR(30)` | NOT NULL, default `'open'` | `open / parsing / indexed / extracting / needs_review / completed / failed` |
| `metadata_json` | `JSONB` | NOT NULL, default `{}` | Arbitrary metadata |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, auto-updated | |

### 2.3 Table: `documents`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `document_id` | `VARCHAR(50)` | PK | UUID (12-char hex) |
| `case_id` | `VARCHAR(50)` | FK → `cases.case_id` ON DELETE CASCADE, NOT NULL, INDEX | |
| `filename` | `VARCHAR(500)` | NOT NULL | Original filename |
| `mime_type` | `VARCHAR(100)` | NOT NULL, default `'application/octet-stream'` | |
| `file_hash` | `VARCHAR(64)` | NULLABLE, INDEX | SHA-256 of file contents |
| `storage_path` | `VARCHAR(1000)` | NULLABLE | Filesystem path |
| `page_count` | `INTEGER` | NOT NULL, default `0` | |
| `user_metadata` | `JSONB` | NOT NULL, default `{}` | |
| `inferred_metadata` | `JSONB` | NOT NULL, default `{}` | Auto-detected metadata |
| `parser_status` | `VARCHAR(30)` | NOT NULL, default `'pending'` | `pending / quick_parsed / parsed / indexed / failed` |
| `parse_quality` | `VARCHAR(20)` | NULLABLE | |
| `priority` | `INTEGER` | NOT NULL, default `0` | |
| `failure_info` | `JSONB` | NULLABLE | |
| `size_bytes` | `INTEGER` | NOT NULL, default `0` | |
| `confidence` | `FLOAT` | NULLABLE | Overall parser confidence 0..1 |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL, auto-updated | |

### 2.4 Table: `pages`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `page_id` | `VARCHAR(50)` | PK | UUID (12-char hex) |
| `document_id` | `VARCHAR(50)` | FK → `documents.document_id` ON DELETE CASCADE, NOT NULL, INDEX | |
| `page_number` | `INTEGER` | NOT NULL | |
| `text` | `TEXT` | NOT NULL, default `''` | Raw text |
| `markdown` | `TEXT` | NULLABLE | Markdown-formatted |
| `image_path` | `VARCHAR(1000)` | NULLABLE | Page image file |
| `width` | `INTEGER` | NULLABLE | |
| `height` | `INTEGER` | NULLABLE | |
| `parse_quality` | `VARCHAR(20)` | NULLABLE | |

### 2.5 Table: `evidence_items`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `evidence_id` | `VARCHAR(50)` | PK | UUID (12-char hex) |
| `case_id` | `VARCHAR(50)` | FK → `cases.case_id` ON DELETE CASCADE, NOT NULL, INDEX | |
| `document_id` | `VARCHAR(50)` | FK → `documents.document_id` ON DELETE CASCADE, NOT NULL, INDEX | |
| `page_id` | `VARCHAR(50)` | FK → `pages.page_id` ON DELETE SET NULL | |
| `page_number` | `INTEGER` | NOT NULL | |
| `source_type` | `VARCHAR(30)` | NOT NULL, default `'text_block'` | `text_block / table_cell / table_row / page / image_region` |
| `text` | `TEXT` | NULLABLE | |
| `markdown` | `TEXT` | NULLABLE | |
| `bbox` | `JSONB` | NULLABLE | `[x, y, w, h]` |
| `metadata_json` | `JSONB` | NOT NULL, default `{}` | |
| `confidence` | `FLOAT` | NULLABLE | |
| `tsv_search` | `TSVECTOR` | NULLABLE | GIN-indexed full-text search |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | |

**Indexes**: `idx_evidence_tsv` (GIN on `tsv_search`), `idx_evidence_case_type` (`case_id`, `source_type`)

### 2.6 Table: `evidence_embeddings`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `embedding_id` | `VARCHAR(50)` | PK | UUID (12-char hex) |
| `evidence_id` | `VARCHAR(50)` | FK → `evidence_items.evidence_id` ON DELETE CASCADE, UNIQUE, NOT NULL, INDEX | 1:1 with evidence |
| `embedding` | `VECTOR(1536)` | NOT NULL | OpenAI `text-embedding-3-small` |
| `embedding_api` | `VECTOR(3072)` | NULLABLE | Legacy API column |

**Indexes**: `idx_embedding_hnsw` (HNSW, cosine), `idx_embedding_api_hnsw` (HNSW, cosine)

### 2.7 Table: `extraction_jobs`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `job_id` | `VARCHAR(50)` | PK | UUID (12-char hex) |
| `case_id` | `VARCHAR(50)` | FK → `cases.case_id` ON DELETE CASCADE, NOT NULL, INDEX | |
| `schema_id` | `VARCHAR(50)` | NOT NULL | |
| `schema_json` | `JSONB` | NULLABLE | Inline schema snapshot |
| `status` | `VARCHAR(30)` | NOT NULL, default `'pending'` | `pending / running / completed / needs_review / failed` |
| `started_at` | `TIMESTAMPTZ` | NULLABLE | |
| `completed_at` | `TIMESTAMPTZ` | NULLABLE | |

### 2.8 Table: `field_results`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `field_result_id` | `VARCHAR(50)` | PK | UUID (12-char hex) |
| `job_id` | `VARCHAR(50)` | FK → `extraction_jobs.job_id` ON DELETE CASCADE, NOT NULL, INDEX | |
| `field_path` | `VARCHAR(200)` | NOT NULL | Dot-notation path (e.g. `invoice.total`) |
| `value` | `JSONB` | NULLABLE | Final extracted value |
| `status` | `VARCHAR(30)` | NOT NULL, default `'missing'` | `validated / missing / conflict / low_confidence / invalid / human_corrected` |
| `confidence` | `FLOAT` | NOT NULL, default `0.0` | |
| `validation_errors` | `JSONB` | NOT NULL, default `[]` | |
| `attempt_count` | `INTEGER` | NOT NULL, default `0` | |

### 2.9 Table: `field_candidates`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `candidate_id` | `VARCHAR(50)` | PK | UUID (12-char hex) |
| `field_result_id` | `VARCHAR(50)` | FK → `field_results.field_result_id` ON DELETE CASCADE, NOT NULL, INDEX | |
| `value` | `JSONB` | NULLABLE | Extracted candidate value |
| `confidence` | `FLOAT` | NOT NULL, default `0.0` | |
| `evidence_ids` | `VARCHAR[]` (ARRAY) | NOT NULL, default `[]` | ID references to evidence_items |
| `extraction_method` | `VARCHAR(30)` | NOT NULL, default `'keyword_rule'` | `regex / keyword_rule / llm_text / vlm_image / table_parser / human` |

### 2.10 Table: `field_attempts`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `attempt_id` | `VARCHAR(50)` | PK | UUID (12-char hex) |
| `field_result_id` | `VARCHAR(50)` | FK → `field_results.field_result_id` ON DELETE CASCADE, NOT NULL, INDEX | |
| `attempt_number` | `INTEGER` | NOT NULL, default `1` | |
| `evidence_pack` | `JSONB` | NOT NULL, default `{}` | Evidence used in this attempt |
| `input_tokens` | `INTEGER` | NULLABLE | LLM input token count |
| `output_tokens` | `INTEGER` | NULLABLE | LLM output token count |
| `model_used` | `VARCHAR(100)` | NULLABLE | |
| `cost` | `NUMERIC(12,6)` | NULLABLE | USD cost |
| `error` | `TEXT` | NULLABLE | |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | |

### 2.11 Table: `document_jobs` (internal queue)

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `job_id` | `VARCHAR(50)` | PK | UUID (12-char hex) |
| `document_id` | `VARCHAR(50)` | FK → `documents.document_id` ON DELETE CASCADE, NOT NULL, INDEX | |
| `task_type` | `VARCHAR(30)` | NOT NULL | `quick_parse / deep_parse / index / extract_ready_fields` |
| `status` | `VARCHAR(20)` | NOT NULL, default `'pending'` | `pending / running / completed / failed` |
| `priority` | `INTEGER` | NOT NULL, default `0` | |
| `error` | `TEXT` | NULLABLE | |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | |
| `started_at` | `TIMESTAMPTZ` | NULLABLE | |
| `completed_at` | `TIMESTAMPTZ` | NULLABLE | |

**Index**: `idx_docjob_status` (`status`, `priority`)

### 2.12 Table: `extraction_results` (Lab cache)

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `run_id` | `VARCHAR(50)` | PK | |
| `input_id` | `VARCHAR(200)` | NOT NULL, INDEX | Input document ID |
| `schema_name` | `VARCHAR(200)` | NOT NULL | |
| `response_json` | `JSONB` | NOT NULL | Full `ExtractionRunResponse` |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | |

---

## 3. API Endpoints

### 3.1 Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Service info |
| `GET` | `/health` | Liveness check |

---

### 3.2 Cases

Base: `/api/cases`

#### `POST /api/cases`
Create a new extraction case.

**Request Body** (`CaseCreate`):
```json
{
  "title": "string",
  "user_id": "string (default: local)",
  "metadata_json": {}
}
```

**Response** `200` (`ExtractionCase`):
```json
{
  "case_id": "string",
  "user_id": "local",
  "title": "string",
  "status": "open",
  "document_ids": [],
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

#### `GET /api/cases`
List all cases.

**Response** `200`: `ExtractionCase[]`

#### `GET /api/cases/{case_id}`
Get case details.

**Response** `200`: `ExtractionCase`

#### `GET /api/cases/{case_id}/progress`
Get case processing progress.

**Response** `200`:
```json
{
  "case_id": "string",
  "documents": { "total": 0 },
  "status": "open"
}
```

#### `GET /api/cases/{case_id}/documents`
List documents in a case.

**Response** `200`: `DocumentMetadata[]`

#### `POST /api/cases/{case_id}/documents`
Upload a document to a case.

**Request**: `multipart/form-data`
- `file`: UploadFile (required)
- `metadata_json`: string (JSON, default: `{}`)

**Response** `200` (`DocumentMetadata`)

#### `POST /api/cases/{case_id}/index`
Trigger indexing of all documents in the case.

**Response** `200`:
```json
[{ "document_id": "string", "status": "pending" }]
```

---

### 3.3 Documents

Base: `/api/documents`

#### `GET /api/documents`
List all documents (from in-memory store).

**Query Parameters**:
| Param | Type | Description |
|-------|------|-------------|
| `source` | `DocumentSource` | Filter: `upload / corporate_db` |
| `type` | `DocumentType` | Filter: `invoice / receipt / contract / report / form / id / other` |
| `collection` | `string` | Filter by collection |
| `q` | `string` | Search by name |

**Response** `200`: `DocumentMetadata[]`

**`DocumentMetadata`**:
```json
{
  "id": "string",
  "name": "string",
  "type": "other",
  "source": "upload",
  "mime_type": "application/pdf",
  "size_bytes": 0,
  "page_count": 0,
  "status": "uploaded",
  "tags": [],
  "collection": null,
  "uploaded_at": "2024-01-01T00:00:00Z",
  "processed_at": null,
  "preview_url": null,
  "confidence": null,
  "notes": null
}
```

**DocumentStatus enum**: `uploaded / queued / processing / ocr_done / reviewed / approved / failed`

#### `POST /api/documents/upload`
Upload a document file.

**Request**: `multipart/form-data` — `file: UploadFile`

**Response** `200` (`DocumentUploadAck`):
```json
{
  "id": "string",
  "name": "string",
  "size_bytes": 0,
  "status": "uploaded",
  "message": "Document uploaded successfully. Run processing to extract data."
}
```

#### `GET /api/documents/{document_id}`
Get document metadata.

**Response** `200`: `DocumentMetadata`

#### `GET /api/documents/{document_id}/evidence`
List all evidence items for a document (DB-backed only).

**Response** `200`: `EvidenceItem[]`

#### `DELETE /api/documents/{document_id}`
Delete a document.

**Response** `200`: `{ "ok": true, "id": "string" }`

#### `POST /api/documents/{document_id}/process`
Trigger OCR/extraction processing.

**Response** `200`: `DocumentMetadata`

---

### 3.4 OCR

Base: `/api/ocr`

#### `GET /api/ocr/{document_id}`
Get OCR result for a document.

**Response** `200` (`OcrResult`):
```json
{
  "id": "string",
  "document_id": "string",
  "engine": "placeholder-ocr",
  "language": "en",
  "pages": 1,
  "blocks": [
    {
      "id": "string",
      "page": 1,
      "type": "text",
      "bbox": [0, 0, 1, 1],
      "text": "string",
      "confidence": 0.95,
      "edited": false,
      "data": null
    }
  ],
  "overall_confidence": 0.0,
  "processed_at": "2024-01-01T00:00:00Z",
  "edited": false,
  "approved": false
}
```

**BlockType enum**: `text / heading / table / key_value / image / signature`

#### `PUT /api/ocr/{document_id}`
Update OCR result (edit blocks, approve).

**Request Body** (`OcrUpdate`):
```json
{
  "blocks": [/* OcrBlock[] */],
  "approved": true,
  "engine": "string"
}
```

**Response** `200`: `OcrResult`

#### `POST /api/ocr/{document_id}/reset`
Reset OCR edits back to original.

**Response** `200`: `OcrResult`

#### `GET /api/ocr/{document_id}/fields`
Derive editable extraction fields from OCR blocks.

**Response** `200`: `EditableExtractionField[]`

---

### 3.5 Templates

Base: `/api/templates`

#### `GET /api/templates`
List all templates.

**Response** `200`: `ExtractionTemplate[]`

#### `POST /api/templates`
Create a new template.

**Request Body** (`TemplateCreate`):
```json
{
  "name": "string",
  "description": null,
  "document_type": "other",
  "fields": [
    {
      "id": "string",
      "label": "string",
      "key": "string",
      "type": "text",
      "example_value": null,
      "validation_rule": null,
      "required": false,
      "notes": null,
      "extraction_hint": null,
      "default_value": null
    }
  ],
  "ocr_method": "advanced-ocr-standard",
  "chunking_strategy": "page-by-page",
  "max_pages": 10,
  "loop_condition": "EOF",
  "source_document_id": null
}
```

**FieldType enum**: `text / number / date / currency / email / phone / select / multiselect / boolean / table / regex`

**Response** `200`: `ExtractionTemplate`

#### `GET /api/templates/{template_id}`
Get template by ID.

#### `PUT /api/templates/{template_id}`
Update template.

**Request Body**: `dict` (partial update).

#### `DELETE /api/templates/{template_id}`
Delete template.

**Response** `200`: `{ "ok": true, "id": "string" }`

---

### 3.6 Batch Template Application

Base: `/api/batch`

#### `POST /api/batch/apply`
Apply a template to multiple documents.

**Request Body** (`BatchApplyRequest`):
```json
{
  "template_id": "string",
  "document_ids": ["doc1", "doc2"]
}
```

**Response** `200` (`BatchProcessingResult`):
```json
{
  "id": "string",
  "template_id": "string",
  "template_name": "string",
  "started_at": "...",
  "finished_at": null,
  "total": 2,
  "done": 0,
  "failed": 0,
  "items": [/* BatchItemResult[] */],
  "average_confidence": 0.0,
  "average_latency_ms": 0.0
}
```

#### `GET /api/batch`
List batch results.

#### `GET /api/batch/{batch_id}`
Get specific batch result.

---

### 3.7 Extraction Schemas

Base: `/api/schemas`

#### `POST /api/schemas`
Create extraction schema.

**Request Body** (`SchemaCreate`):
```json
{
  "name": "string",
  "json_schema": {
    "type": "object",
    "properties": { /* JSON Schema */ }
  },
  "field_hints": {
    "field.path": {
      "field_path": "field.path",
      "description": "",
      "expected_document_types": [],
      "keywords": [],
      "likely_regions": [],
      "value_type": "text",
      "allow_multiple_sources": true,
      "conflict_policy": "human_review_on_disagreement"
    }
  },
  "user_id": "local"
}
```

**ConflictPolicy enum**: `first_high_confidence / highest_confidence / human_review_on_disagreement / allow_multiple_values`

**Response** `200` (`ExtractionSchema`):
```json
{
  "schema_id": "string",
  "user_id": "local",
  "name": "string",
  "json_schema": {},
  "field_hints": {},
  "version": 1,
  "created_at": "...",
  "updated_at": "..."
}
```

#### `GET /api/schemas`
List all schemas.

#### `GET /api/schemas/{schema_id}`
Get schema by ID.

#### `PUT /api/schemas/{schema_id}`
Update schema.

**Request Body** (`SchemaUpdate`):
```json
{
  "name": "string (optional)",
  "json_schema": {},
  "field_hints": {}
}
```

#### `POST /api/schemas/{schema_id}/validate`
Validate an existing schema.

#### `POST /api/schemas/validate`
Validate an arbitrary JSON schema.

**Request Body**: `dict`
**Response** (`SchemaValidationResult`):
```json
{
  "valid": true,
  "errors": [],
  "field_paths": ["field1", "field2"]
}
```

---

### 3.8 Extraction (Case-level)

Base: `/api/cases/{case_id}`

#### `POST /api/cases/{case_id}/search`
Hybrid search across case evidence.

**Request Body** (`SearchRequest`):
```json
{
  "query": "string",
  "top_k": 8
}
```

**Response** `200`:
```json
[
  {
    "score": 0.95,
    "evidence": { /* EvidenceSource */ }
  }
]
```

**`EvidenceSource`**:
```json
{
  "evidence_id": "string",
  "document_id": "string",
  "filename": "string",
  "page_number": 1,
  "source_type": "text_block",
  "text": "string | null",
  "bbox": [0, 0, 1, 1] | null,
  "confidence": 0.95 | null
}
```

#### `POST /api/cases/{case_id}/extract`
Run extraction on a case.

**Request Body** (`ExtractionRequest`):
```json
{
  "schema_id": "string",
  "schema_json": { /* JSON Schema (optional, alias for output_schema) */ },
  "max_evidence_per_field": 8,
  "baseline": false
}
```

**Response** `200` (`ExtractionResult`):
```json
{
  "job_id": "string",
  "case_id": "string",
  "schema_id": "string",
  "status": "running",
  "fields": {
    "field.path": {
      "field_path": "field.path",
      "value": null,
      "status": "missing",
      "confidence": 0.0,
      "selected_candidate_id": null,
      "candidates": [],
      "validation_errors": []
    }
  },
  "final_json": {},
  "validation_report": {},
  "started_at": "...",
  "completed_at": null
}
```

**ExtractionJobStatus enum**: `queued / running / completed / needs_review / failed`  
**FieldStatus enum**: `validated / missing / conflict / low_confidence / invalid / human_corrected`

#### `POST /api/cases/{case_id}/extract-baseline`
Same as extract but forced baseline mode.

#### `GET /api/extraction-jobs/{job_id}`
Get job status and results.

#### `GET /api/extraction-jobs/{job_id}/candidates`
List all field candidates for a job.

#### `POST /api/extraction-jobs/{job_id}/fields/{field_path}/retry`
Retry extraction for a specific field.

#### `GET /api/extraction-jobs/{job_id}/export`
Export full job results.

**Response** `200` (`ExportBundle`):
```json
{
  "final_json": {},
  "parsed_markdown": "string",
  "evidence_report": {},
  "validation_report": {},
  "review_log": []
}
```

#### `POST /api/extraction-jobs/{job_id}/export-files`
Write export files to disk.

**Response** `200`:
```json
{ "files": ["/path/to/file.json"] }
```

---

### 3.9 Human Review

Base: `/api/extraction-jobs/{job_id}`

#### `GET /api/extraction-jobs/{job_id}/review`
Get review payload.

**Response** `200` (`ReviewPayload`):
```json
{
  "job": { /* ExtractionResult */ },
  "review_required_fields": ["field.path"],
  "actions": []
}
```

#### `POST /api/extraction-jobs/{job_id}/fields/{field_path}/approve`
Approve a field value.

**Request Body** (`ApproveFieldRequest`):
```json
{
  "reviewer_id": "local",
  "reason": "string | null"
}
```

**Response** `200`: `FieldResult`

#### `POST /api/extraction-jobs/{job_id}/fields/{field_path}/correct`
Correct a field value.

**Request Body** (`CorrectFieldRequest`):
```json
{
  "corrected_value": "any",
  "reviewer_id": "local",
  "reason": "string | null"
}
```

**Response** `200`: `FieldResult`

#### `POST /api/extraction-jobs/{job_id}/finalize`
Finalize an extraction job.

**Response** `200`: `ExtractionResult`

**ReviewActionType enum**: `approve / correct / mark_missing / mark_not_applicable`

---

### 3.10 Benchmarks

Base: `/api/benchmarks`

#### `GET /api/benchmarks`
List all benchmark runs.

#### `GET /api/benchmarks/runs`
List run summaries.

#### `POST /api/benchmarks`
Create a benchmark run (currently generates mock metrics).

**Request Body** (`BenchmarkRunCreate`):
```json
{
  "template_id": "string",
  "document_ids": ["doc1"],
  "repeat": 1
}
```

**Response** `200` (`BenchmarkRun`):
```json
{
  "id": "string",
  "run_id": "RUN-2024-0001",
  "template_id": "string",
  "template_name": "string",
  "document_ids": [],
  "status": "completed",
  "started_at": "...",
  "finished_at": null,
  "metrics": [
    {
      "name": "field_level_accuracy",
      "label": "Field-level Accuracy",
      "value": 0.95,
      "unit": "ratio",
      "target": 0.95
    }
  ],
  "field_metrics": [],
  "consistency_samples": [],
  "notes": null
}
```

#### `GET /api/benchmarks/{run_id}`
Get benchmark run details.

---

### 3.11 Parser Benchmarks

Base: `/api/parser-benchmarks`

#### `GET /api/parser-benchmarks/inputs`
List available parser inputs (uploaded files).

**Response** `200`: `ParserInputInfo[]`

```json
{
  "id": "string",
  "name": "filename.pdf",
  "input_type": "pdf",
  "size_bytes": 12345,
  "path": "/path/to/file",
  "page_count": 5
}
```

#### `GET /api/parser-benchmarks/preview/{input_id}`
Serve the original file for preview.

**Response**: File (inline)

#### `GET /api/parser-benchmarks/preview-text/{input_id}`
Get text preview (txt/md/csv/docx only).

**Response**: Plain text

#### `GET /api/parser-benchmarks/preview-page/{input_id}`
Render a specific page as PNG.

**Query Parameters**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page` | int (≥1) | `1` | Page number |
| `zoom` | float (0.5–4.0) | `1.4` | Zoom factor |

**Response**: PNG image

#### `GET /api/parser-benchmarks/media/{media_path}`
Serve parser-generated media files.

#### `GET /api/parser-benchmarks/parsers`
List available parsers.

**Response** `200`:
```json
[
  {
    "id": "mistral_ocr",
    "name": "Mistral OCR",
    "supported_input_types": ["pdf", "png", "jpg"],
    "installed": true,
    "notes": null
  }
]
```

Supported parsers: `mistral_ocr`, `docling`, `pymupdf`, `pdfplumber`, `layout_pdfplumber`, `pypdf`, `paddle_ocr`, `paddleocr_vl`, `paddleocr_vl_local`, `paddleocr_vl_vllm`, `doclayout_yolo_demo`, `unstructured`, `pillow`, `pdf_extract_kit`, `plain_text`

#### `GET /api/parser-benchmarks/runs`
List all benchmark runs.

**Response** `200`: `ParserRunSummary[]`

#### `POST /api/parser-benchmarks/run`
Run parser benchmark.

**Request Body** (`ParserRunRequest`):
```json
{
  "input_id": "string",
  "parsers": ["mistral_ocr", "pymupdf"],
  "preview_chars": 1500
}
```

Leave `parsers` empty to run all installed parsers.

**Response** `200` (`ParserRunResponse`):
```json
{
  "run_id": "string",
  "input": { /* ParserInputInfo */ },
  "results": [
    {
      "result_id": "string",
      "run_id": "string",
      "library": "mistral_ocr",
      "input_file": "file.pdf",
      "input_type": "pdf",
      "status": "ok",
      "seconds": 2.5,
      "pages": 5,
      "chars": 10000,
      "tables": 3,
      "images": 0,
      "error": null,
      "text_preview": "...",
      "structured_preview": {},
      "artifact_paths": {
        "output_md": "/path/to/output.md",
        "structured_json": "/path/to/structured.json",
        "corrections_json": null
      }
    }
  ],
  "started_at": "...",
  "finished_at": "..."
}
```

#### `GET /api/parser-benchmarks/runs/{run_id}`
Get run details.

#### `GET /api/parser-benchmarks/runs/{run_id}/results/{library}`
Get detailed result for a specific parser.

**Response** `200` (`ParserResultDetail`):
```json
{
  "run": { /* ParserRunResponse */ },
  "result": { /* ParserRunResult */ },
  "full_text": "string",
  "ground_truth": {
    "input_id": "string",
    "input_name": "",
    "expected_terms": [],
    "expected_fields": [],
    "notes": "",
    "updated_at": "..."
  },
  "corrections": {
    "corrected_text": "",
    "notes": "",
    "updated_at": "..."
  },
  "quality_checks": []
}
```

#### `GET /api/parser-benchmarks/runs/{run_id}/results/{library}/cleaned-evidence`
Get cleaned evidence for a parser result.

#### `GET /api/parser-benchmarks/ground-truth/{input_id}`
Get ground truth for an input.

#### `PUT /api/parser-benchmarks/ground-truth/{input_id}`
Update ground truth.

**Request Body** (`ParserGroundTruth`):
```json
{
  "input_id": "string",
  "input_name": "",
  "expected_terms": ["term1"],
  "expected_fields": [{"key": "field1", "label": "Field 1", "value": "expected"}],
  "notes": "",
  "updated_at": "..."
}
```

#### `PUT /api/parser-benchmarks/runs/{run_id}/results/{library}/corrections`
Save manual corrections for a parser result.

**Request Body** (`ParserCorrection`):
```json
{
  "corrected_text": "string",
  "notes": "string",
  "updated_at": "..."
}
```

---

### 3.12 Extraction Lab

Base: `/api/extraction-lab`

#### `GET /api/extraction-lab/inputs`
List available uploads for extraction.

#### `GET /api/extraction-lab/parsers`
List parsers available for extraction (same as parser-benchmarks/parsers, with `plain_text` added).

#### `GET /api/extraction-lab/schemas`
List schema templates.

**Response** `200`: `ExtractionLabSchemaTemplate[]`

#### `POST /api/extraction-lab/schemas`
Save a schema template.

**Request Body** (`ExtractionLabSchema`):
```json
{
  "name": "ExtractionResult",
  "description": null,
  "fields": [
    {
      "id": "",
      "key": "invoice_number",
      "label": "Invoice Number",
      "type": "text",
      "description": null,
      "required": false,
      "children": []
    }
  ]
}
```

**ExtractionFieldType enum**: `text / number / date / currency / email / phone / boolean / list / table / object`

**Response** `200`: `ExtractionLabSchemaTemplate`

#### `DELETE /api/extraction-lab/schemas/{schema_id}`
Delete a schema template.

#### `POST /api/extraction-lab/upload`
Upload a single file.

**Request**: `multipart/form-data` — `file: UploadFile`

**Response** `200`: `ParserInputInfo`

#### `POST /api/extraction-lab/upload-multiple`
Upload multiple files.

**Request**: `multipart/form-data` — `files: UploadFile[]`

**Response** `200`: `ParserInputInfo[]`

#### `POST /api/extraction-lab/run`
Run a single-document extraction.

**Request Body** (`ExtractionRunRequest`):
```json
{
  "input_id": "string",
  "output_schema": {
    "name": "ExtractionResult",
    "fields": [
      { "key": "field1", "label": "Field 1", "type": "text", "description": null, "required": false }
    ]
  },
  "natural_language_query": null,
  "parser_id": "auto",
  "chunking_strategy": "page",
  "chunk_size": 500,
  "chunk_overlap": 80,
  "max_pages": 50,
  "max_candidates_per_field": 8,
  "preview_chars": 6000,
  "extraction_tier": "cost_effective"
}
```

**ExtractionTier enum**: `cost_effective / agentic / agentic_plus`  
**ChunkingStrategy**: `page / sliding_window / document / table_row / block`

**Response** `200` (`ExtractionRunResponse`):
```json
{
  "run_id": "string",
  "input": { /* ParserInputInfo */ },
  "parser_id": "auto",
  "parser_name": "Mistral OCR",
  "parser_run_id": null,
  "parser_run_started_at": null,
  "extraction_tier": "cost_effective",
  "schema_model_name": "ExtractionResult",
  "schema_definition": { /* dict */ },
  "natural_language_query": null,
  "data": { "field1": "value" },
  "fields": [
    {
      "key": "field1",
      "label": "Field 1",
      "type": "text",
      "required": false,
      "value": "extracted",
      "raw_value": "extracted",
      "confidence": 0.95,
      "valid": true,
      "validation_message": null,
      "evidence": [
        {
          "chunk_id": "string",
          "page": 1,
          "type": "text_block",
          "text_preview": "...",
          "bbox": null,
          "source_url": null
        }
      ]
    }
  ],
  "chunks": [ /* ExtractionChunk[] */ ],
  "validation_errors": [],
  "warnings": [],
  "generated_code": "def ExtractionResult(...)",
  "stats": {
    "parser_seconds": 1.5,
    "total_seconds": 2.0,
    "pages": 5,
    "chunks": 10,
    "fields": 1,
    "candidates_scanned": 3,
    "chunking_strategy": "page",
    "chunk_tokens": 0,
    "retrieval_mode": "unknown",
    "dense_hits": 0,
    "sparse_hits": 0,
    "null_fields_detected": 0,
    "null_retries": 0,
    "recovered_nulls": 0,
    "candidate_conflicts": 0,
    "critic_issues": 0,
    "consistency_score": 1.0,
    "agentic_used": false,
    "adk_available": false,
    "model_used": null
  },
  "started_at": "...",
  "finished_at": "..."
}
```

#### `POST /api/extraction-lab/run-multi`
Run multi-document extraction.

**Request Body** (`MultiDocumentExtractionRunRequest`) — extends `ExtractionRunRequest`:
```json
{
  "input_ids": ["id1", "id2"],
  "multi_document_mode": "per_document",
  "...": "same fields as ExtractionRunRequest"
}
```

**MultiDocumentMode enum**: `per_document / cross_document`

**Response** `200`:
```json
{
  "mode": "per_document",
  "results": [ /* ExtractionRunResponse[] */ ]
}
```

#### `POST /api/extraction-lab/generate-schema`
Auto-generate an extraction schema from documents.

**Request Body** (`SchemaGenerationRequest`):
```json
{
  "input_ids": ["id1"],
  "natural_language_query": "Extract invoice total",
  "parser_id": "auto",
  "multi_document_mode": "per_document",
  "chunking_strategy": "page",
  "chunk_size": 500,
  "chunk_overlap": 80,
  "max_pages": 20,
  "preview_chars": 8000
}
```

**Response** `200`:
```json
{
  "schema_definition": { /* ExtractionLabSchema */ },
  "warnings": []
}
```

#### `POST /api/extraction-lab/report`
Generate a polished markdown report from extraction results.

**Request Body** (`ExtractionReportRequest`):
```json
{ "result": { /* ExtractionRunResponse */ } }
```

**Response** `200`:
```json
{ "report_markdown": "string" }
```

#### `GET /api/extraction-lab/results/{input_id}`
Get all extraction results for an input.

#### `GET /api/extraction-lab/history`
Get extraction job history (DB-backed only).

**Response** `200`: `JobHistoryItem[]`

```json
{
  "job_id": "string",
  "filename": "string",
  "status": "SUCCESS",
  "tier": "Cost Effective",
  "queue_time": "100 ms",
  "processing_time": "2.5s",
  "total_time": "2.6s",
  "estimated_cost_usd": 0.001,
  "created_at": "Jan 1, 2024, 12:00 PM",
  "result_run_id": "string | null"
}
```

#### `GET /api/extraction-lab/results/job/{run_id}`
Get extraction result by run ID.

#### `DELETE /api/extraction-lab/inputs/{input_id}`
Delete an uploaded input file.

#### `DELETE /api/extraction-lab/results/{run_id}`
Delete a result and its associated job/case (if no remaining jobs).

---

## 4. Extraction Tier Descriptions

| Tier | Description |
|------|-------------|
| `cost_effective` | Single LLM call per field (GPT-5-mini), regex fallback, no retry |
| `agentic` | Google ADK agent loop: planner → extractor → critic → retry (up to 3 attempts) |
| `agentic_plus` | Same as agentic + cross-field consistency check + conflict resolution |

## 5. Extraction Pipeline Flow

```
Upload → Parse (selected parser) → Chunk (text/table blocks)
  → Clean evidence → Hybrid retrieval (dense + BM25 + RRF)
    → Per-field extraction (LLM first, regex fallback)
      → Type coercion → Dynamic Pydantic validation
        → Return ExtractionRunResponse
```

Retrieval strategies tried in order: dense (vector) → sparse (BM25) → FTS fallback.

## 6. Supported Parsers

| ID | Engine | Input Types |
|----|--------|-------------|
| `mistral_ocr` | Mistral AI OCR API | PDF, PNG, JPG |
| `docling` | Docling (local or microservice) | PDF |
| `pymupdf` | PyMuPDF (fitz) | PDF |
| `pdfplumber` | pdfplumber | PDF |
| `layout_pdfplumber` | pdfplumber + layout detection | PDF |
| `pypdf` | pypdf | PDF |
| `paddle_ocr` | Local PaddleOCR | PNG, JPG |
| `paddleocr_vl` | PaddleOCR-VL API | PDF, PNG, JPG |
| `paddleocr_vl_local` | PaddleOCR-VL (llama.cpp) | PDF, PNG, JPG |
| `paddleocr_vl_vllm` | PaddleOCR-VL (vLLM) | PDF, PNG, JPG |
| `doclayout_yolo_demo` | DocLayout-YOLO | PDF |
| `unstructured` | Unstructured.io | PDF, DOCX |
| `pillow` | Pillow (basic image OCR) | PNG, JPG |
| `pdf_extract_kit` | PDF-Extract-Kit (external) | PDF |
| `plain_text` | Built-in text reader | TXT, CSV, MD, TSV, JSON |

---

## 7. Caddy Gateway

Production deployment uses a Caddy reverse proxy on port `81`:

| Route | Target | Description |
|-------|--------|-------------|
| `/` | `localhost:3000` | Next.js frontend |
| `/api/*` | `localhost:8000` | FastAPI backend |
| `?XTransformPort` query param switches between 3000/8000 for development |
