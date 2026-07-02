# AGENT.md — Production-Ready Document Parsing + Schema-Based Extraction Platform

**Purpose:** This file is written for a coding agent that will build a LlamaParse/LlamaExtract-like document extraction workflow.

The goal is **not** just PDF text parsing. The goal is a production-ready pipeline that can:

1. Accept one or many uploaded documents.
2. Parse each document into text, Markdown, tables, layout blocks, page images, and metadata.
3. Let the user define or auto-generate an extraction schema.
4. Extract structured data according to that schema.
5. Avoid burning the LLM context window by using page/block/table retrieval instead of whole-document prompting.
6. Attach evidence/provenance to every extracted field.
7. Detect conflicts when the same field appears in multiple documents or pages.
8. Validate results with Pydantic/JSON Schema and business rules.
9. Support human review for uncertain or invalid fields.
10. Export final JSON, parsed Markdown, evidence snippets, and validation reports.

---

## 0. Key Terms

### Parsing

Parsing means transforming an input document into machine-usable structure.

Examples:

```text
PDF / DOCX / image
→ text
→ Markdown
→ tables
→ layout blocks
→ page numbers
→ bounding boxes
→ rendered page images
```

Libraries in this layer:

- `pypdf`
- `PyMuPDF`
- `pdfplumber`
- `Docling`
- `Marker`
- `MinerU`
- `Unstructured`
- OCR engines such as PaddleOCR, Tesseract, EasyOCR, RapidOCR, Surya OCR

### Structured Extraction

Structured extraction means taking parsed content and producing a specific schema-compliant output.

Example:

```json
{
  "invoice_number": "INV-2026-001",
  "invoice_date": "2026-06-20",
  "total_amount": 1320.0,
  "currency": "USD"
}
```

This usually requires:

- LLM/VLM structured output
- rules/regex
- retrieval
- schema validation
- conflict resolution
- provenance/citations

### Validation

Validation checks whether the extracted object is usable.

Validation includes:

- type checks: number/date/string/list/object
- required-field checks
- enum checks
- regex checks
- business rules
- cross-field consistency
- source evidence requirement
- confidence thresholds

### Provenance / Grounding / Citation

Every extracted value should carry its source:

```json
{
  "value": 1320.0,
  "source_document_id": "doc_001",
  "source_filename": "invoice.pdf",
  "page": 3,
  "evidence_text": "Grand Total: USD 1,320.00",
  "bbox": [420.1, 701.4, 560.9, 724.2],
  "confidence": 0.94
}
```

This is crucial for debugging, trust, compliance, and human review.

---

## 1. Recommended High-Level Architecture

Build this as a modular pipeline:

```text
Upload documents
    ↓
Document storage
    ↓
Parse each document
    ↓
Classify document / split bundles
    ↓
Normalize into common Document IR
    ↓
Index pages, blocks, tables, and entities
    ↓
User schema / auto-generated schema
    ↓
Schema-aware extraction planner
    ↓
Field-level evidence retrieval
    ↓
Candidate extraction
    ↓
Candidate merge + conflict resolution
    ↓
Pydantic / JSON Schema validation
    ↓
Human review for failed/low-confidence/conflicted fields
    ↓
Final JSON export + evidence report
```

Important: **do not send the entire document bundle to the LLM as one giant prompt** except for very small documents.

Instead, use:

```text
field → retrieve relevant evidence → extract field → validate field
```

---

## 2. Target System Behavior

The platform should support these user workflows:

### Workflow A — Single document, known schema

Example:

```text
User uploads invoice.pdf
User selects InvoiceSchema
System returns invoice JSON with citations
```

### Workflow B — Multiple documents, one case-level schema

Example:

```text
User uploads:
- medical_invoice.pdf
- doctor_report.pdf
- insurance_form.pdf
- receipt.pdf

User selects ClaimSchema
System fills one claim JSON from multiple documents.
```

Important behavior:

- The same field may appear in multiple documents.
- Some fields may only appear in one document.
- Some values may conflict.
- Final output must show all evidence.

### Workflow C — User-defined custom schema

Example:

```text
User defines fields:
- patient_name: string
- treatment_date: date
- invoice_total: number
- diagnosis: string | null
```

System should:

1. Convert schema to JSON Schema / Pydantic model.
2. Use field descriptions to retrieve evidence.
3. Extract values field-by-field.
4. Validate output.
5. Show missing/conflicting/low-confidence fields.

### Workflow D — Auto-generate schema from examples

Example:

```text
User uploads 5 similar invoices and asks system to generate extraction schema.
```

System should:

1. Sample pages/documents.
2. Infer candidate fields.
3. Propose a schema.
4. Let user edit/approve.
5. Run extraction job using approved schema.

### Workflow E — Human-in-the-loop review

Human reviewer should see:

- final field value
- confidence
- validation status
- source document/page
- evidence snippet
- PDF page preview
- highlighted bounding box when available
- alternatives/candidate values
- conflict warnings

---

## 3. Core Design Principle: Case-Level Extraction, Not Only Document-Level Extraction

A real production system often receives a **bundle** of files, not one isolated file.

Example schema:

```python
class ClaimSchema(BaseModel):
    patient_name: str
    treatment_date: date
    provider_name: str
    invoice_total: float
    diagnosis: str | None
```

The fields may be distributed like this:

```text
patient_name       → insurance_form.pdf, doctor_report.pdf
reatment_date      → doctor_report.pdf, invoice.pdf
provider_name      → invoice.pdf
invoice_total      → invoice.pdf, receipt.pdf
diagnosis          → doctor_report.pdf
```

Therefore use this mental model:

```text
case_id
  ├── document_1
  │   ├── pages
  │   ├── blocks
  │   └── tables
  ├── document_2
  │   ├── pages
  │   ├── blocks
  │   └── tables
  └── extraction_result
      ├── fields
      ├── candidates
      ├── evidence
      └── validation report
```

Do not assume:

```text
one uploaded document = one final JSON object
```

Better:

```text
one case bundle = one final schema object
```

---

## 4. Internal Representation: Document IR

Create a normalized intermediate representation independent of the parser used.

### Document

```python
class ParsedDocument(BaseModel):
    document_id: str
    case_id: str
    filename: str
    mime_type: str
    parser_name: str
    parser_version: str | None = None
    page_count: int
    document_type: str | None = None
    parse_quality: str | None = None
    pages: list[ParsedPage]
    tables: list[ParsedTable] = []
    images: list[ParsedImage] = []
    metadata: dict = {}
```

### Page

```python
class ParsedPage(BaseModel):
    document_id: str
    page_number: int
    text: str
    markdown: str | None = None
    blocks: list[TextBlock]
    width: float | None = None
    height: float | None = None
    image_path: str | None = None
```

### Block

```python
class TextBlock(BaseModel):
    block_id: str
    document_id: str
    page_number: int
    block_type: Literal["title", "paragraph", "table", "figure", "header", "footer", "list", "unknown"]
    text: str
    bbox: list[float] | None = None
    reading_order: int | None = None
    section_title: str | None = None
```

### Table

```python
class ParsedTable(BaseModel):
    table_id: str
    document_id: str
    page_number: int
    caption: str | None = None
    bbox: list[float] | None = None
    rows: list[dict]
    markdown: str | None = None
    html: str | None = None
```

### Evidence Source

```python
class EvidenceSource(BaseModel):
    evidence_id: str
    document_id: str
    filename: str
    page_number: int
    source_type: Literal["text_block", "table_cell", "table_row", "page", "image_region"]
    text: str | None = None
    bbox: list[float] | None = None
    confidence: float | None = None
```

---

## 5. Storage Model

Use persistent storage for uploaded documents, parsed outputs, extraction runs, and review data.

### Suggested database tables / collections

```text
cases
  - case_id
  - user_id
  - title
  - status
  - created_at

uploaded_documents
  - document_id
  - case_id
  - filename
  - mime_type
  - storage_path
  - page_count
  - status
  - created_at

parsed_pages
  - page_id
  - document_id
  - page_number
  - text
  - markdown
  - image_path
  - metadata

parsed_blocks
  - block_id
  - document_id
  - page_number
  - block_type
  - text
  - bbox
  - reading_order
  - embedding_id

parsed_tables
  - table_id
  - document_id
  - page_number
  - rows_json
  - markdown
  - bbox

schemas
  - schema_id
  - user_id
  - name
  - json_schema
  - pydantic_model_source_optional
  - version

extraction_jobs
  - job_id
  - case_id
  - schema_id
  - status
  - started_at
  - completed_at

extraction_fields
  - field_result_id
  - job_id
  - field_path
  - value_json
  - status
  - confidence
  - validation_errors

field_candidates
  - candidate_id
  - field_result_id
  - value_json
  - confidence
  - source_document_id
  - page_number
  - evidence_text
  - bbox

human_review_actions
  - review_id
  - job_id
  - field_path
  - old_value
  - corrected_value
  - reviewer_id
  - reason
  - created_at
```

---

## 6. Parsing Layer

### Supported parsers

Implement parser adapters behind a common interface.

```python
class BaseParser(Protocol):
    def parse(self, file_path: str, case_id: str, document_id: str) -> ParsedDocument:
        ...
```

### Recommended parser stack

#### Default parser: Docling

Use Docling for:

- PDF to Markdown
- structured document conversion
- layout-aware output
- tables
- OCR integration depending on setup

#### Layout/coordinate parser: PyMuPDF

Use PyMuPDF for:

- page rendering
- text blocks
- word coordinates
- bounding boxes
- PDF page previews
- highlighting evidence in UI

#### Table fallback: pdfplumber

Use pdfplumber for:

- line/cell-based table extraction
- coordinate inspection
- table debugging

#### Simple fallback: pypdf

Use pypdf only for:

- simple text extraction
- metadata
- splitting/merging pages

#### OCR fallback

Use OCR when text extraction quality is poor.

Options:

- PaddleOCR
- Tesseract
- RapidOCR
- EasyOCR
- Surya OCR
- cloud OCR such as Mistral OCR, Google Document AI OCR, AWS Textract, Azure Document Intelligence

### Parse quality detection

Add a parse quality scorer.

Signals:

```text
- extracted text length per page
- percentage of garbled characters
- table detection count
- OCR confidence if available
- number of pages with empty text
- language mismatch
- scanned page detection
```

Example:

```python
class ParseQualityReport(BaseModel):
    quality: Literal["good", "medium", "poor"]
    empty_pages: list[int]
    scanned_likely: bool
    avg_text_chars_per_page: float
    warnings: list[str]
```

If parse quality is poor, rerun with OCR or a managed parser.

---

## 7. Document Classification and Bundle Splitting

Before extraction, classify documents.

### Document-level classification

Classify each uploaded document:

```text
invoice
receipt
bank_statement
contract
medical_report
insurance_form
identity_document
research_paper
unknown
```

### Page-level classification

Some PDFs contain multiple documents in one file. Add a splitter.

Example:

```text
combined_claim_packet.pdf
  pages 1-2: claim form
  pages 3-4: medical invoice
  pages 5-6: doctor report
  page 7: receipt
```

Create virtual sub-documents:

```python
class DocumentSegment(BaseModel):
    segment_id: str
    parent_document_id: str
    page_start: int
    page_end: int
    predicted_type: str
    confidence: float
```

This is important for production because users often upload scanned bundles.

---

## 8. Schema System

Support three schema paths:

1. Predefined schema templates.
2. User-defined schema.
3. Auto-generated schema from examples.

### Schema format

Internally store schemas as JSON Schema.

Example:

```json
{
  "type": "object",
  "properties": {
    "invoice_number": {
      "type": "string",
      "description": "Invoice identifier, usually near the top of the invoice."
    },
    "invoice_date": {
      "type": "string",
      "format": "date",
      "description": "Date when invoice was issued."
    },
    "total_amount": {
      "type": "number",
      "description": "Final amount payable, not subtotal or tax alone."
    }
  },
  "required": ["invoice_number", "total_amount"]
}
```

### Field metadata

Extend basic JSON Schema with extraction hints.

```json
{
  "field_path": "total_amount",
  "description": "Final amount payable, not subtotal or tax alone.",
  "expected_document_types": ["invoice", "receipt"],
  "keywords": ["grand total", "amount due", "balance due", "total payable"],
  "likely_regions": ["bottom-right", "summary table"],
  "value_type": "currency_amount",
  "allow_multiple_sources": true,
  "conflict_policy": "highest_confidence_with_review_on_disagreement"
}
```

These hints help retrieval and extraction.

---

## 9. Extraction Strategies

Implement multiple extraction strategies. Route fields to strategies based on schema hints, document type, and parse output.

### Strategy A: Whole-document extraction

Use only for tiny documents.

```text
short document → LLM → schema JSON
```

Use when:

- document has 1–3 pages
- parsed text is short
- schema is small
- no tables or multi-document bundle

Avoid for long documents.

### Strategy B: Page-level extraction

```text
page 1 → partial extraction
page 2 → partial extraction
...
merge partial outputs
```

Use when:

- pages are semi-independent
- fields may appear on specific pages
- line items continue across pages

### Strategy C: Field-level retrieval extraction

This is the recommended default.

```text
schema field → search query → evidence chunks → extract field → validate field
```

For each field:

1. Build search queries from field name, description, keywords, and examples.
2. Retrieve relevant blocks/tables/pages.
3. Extract candidate values.
4. Store evidence.
5. Validate.

### Strategy D: Table-first extraction

Use for line items, bank transactions, financial statements, invoices, and statements.

```text
tables → normalize rows/columns → extract schema fields from table data
```

Use LLM only for:

- ambiguous column names
- broken rows
- merged cells
- table continuation across pages

### Strategy E: Layout-region extraction

Use coordinates and page images.

Example:

```text
invoice_number → header area
total_amount → bottom-right summary region
signature → bottom region
```

This requires PyMuPDF/pdfplumber/Docling layout metadata.

### Strategy F: VLM-assisted extraction

Use for:

- scanned documents
- photos
- forms
- checkboxes
- stamps
- signatures
- messy tables
- visual-only information

Do not use VLM on every page by default. Use it selectively.

### Strategy G: Candidate generation + resolver

Generate multiple candidate values per field, then resolve.

Example:

```json
[
  {"value": 1200.0, "label": "Subtotal", "confidence": 0.45},
  {"value": 1320.0, "label": "Grand Total", "confidence": 0.91},
  {"value": 120.0, "label": "Tax", "confidence": 0.52}
]
```

Resolver chooses final answer based on:

- label semantics
- field description
- validation rules
- source reliability
- cross-document agreement
- arithmetic checks

---

## 10. Schema-Aware Evidence Retrieval

Do not extract from all content. Retrieve relevant evidence for each field.

### Build indexes

Create multiple indexes:

1. Full-text keyword index.
2. Vector embedding index.
3. Table index.
4. Entity index.
5. Layout/page-region index.

### Hybrid retrieval score

```text
score = semantic_similarity
      + keyword_match_score
      + document_type_bonus
      + page_position_bonus
      + layout_region_bonus
      + table_relevance_bonus
```

Example for `total_amount`:

```text
keywords:
- total
- grand total
- amount due
- balance due
- payable
- net amount

layout bonus:
- bottom of invoice
- summary table
- last page
```

Example for `parties` in contract:

```text
keywords:
- between
- by and between
- party
- agreement entered into

page bonus:
- first 3 pages
```

---

## 11. Candidate Data Model

Every extraction should return candidates before final output.

```python
class FieldCandidate(BaseModel):
    field_path: str
    value: Any
    normalized_value: Any | None = None
    confidence: float
    evidence: list[EvidenceSource]
    extraction_method: Literal[
        "regex",
        "keyword_rule",
        "llm_text",
        "vlm_image",
        "table_parser",
        "human"
    ]
    validation_errors: list[str] = []
```

Final field result:

```python
class FieldResult(BaseModel):
    field_path: str
    value: Any | None
    status: Literal["validated", "missing", "conflict", "low_confidence", "invalid", "human_corrected"]
    confidence: float
    selected_candidate_id: str | None = None
    candidates: list[FieldCandidate]
    validation_errors: list[str] = []
```

Final extraction result:

```python
class ExtractionResult(BaseModel):
    job_id: str
    case_id: str
    schema_id: str
    status: Literal["completed", "needs_review", "failed"]
    fields: dict[str, FieldResult]
    final_json: dict
    validation_report: dict
```

---

## 12. Conflict Resolution

When the same field appears in multiple documents, do not silently pick one.

### Conflict examples

```text
invoice.pdf says total_amount = 850.00
receipt.pdf says total_amount = 820.00
```

```text
insurance_form.pdf says patient_name = John Smith
doctor_report.pdf says patient_name = Jon Smith
```

### Conflict policies

Support policies per field:

```text
first_high_confidence
highest_confidence
prefer_document_type
prefer_newest_document
must_agree_across_sources
human_review_on_disagreement
allow_multiple_values
```

Example field metadata:

```json
{
  "field_path": "invoice_total",
  "conflict_policy": "human_review_on_disagreement",
  "preferred_document_types": ["invoice", "receipt"]
}
```

### Conflict result format

```json
{
  "invoice_total": {
    "status": "conflict",
    "selected_value": null,
    "candidates": [
      {
        "value": 850.0,
        "source_document": "invoice.pdf",
        "page": 2,
        "evidence": "Total Amount Due: $850.00"
      },
      {
        "value": 820.0,
        "source_document": "receipt.pdf",
        "page": 1,
        "evidence": "Paid: $820.00"
      }
    ],
    "review_required": true
  }
}
```

---

## 13. Validation System

Use layered validation.

### Layer 1: Schema validation

Use Pydantic or JSON Schema.

Checks:

- required fields
- types
- date format
- number format
- enum values
- nested object/list structure

### Layer 2: Normalization

Normalize:

- currencies
- dates
- numbers
- phone numbers
- addresses
- names
- IDs

Example:

```text
"USD 1,320.00" → { amount: 1320.0, currency: "USD" }
"20/06/2026" → "2026-06-20"
```

### Layer 3: Business rules

Examples:

```text
invoice_total >= 0
invoice_date <= today
sum(line_items.amount) + tax ≈ total_amount
patient_name must match across claim form and invoice, or else review
currency must be one of supported currencies
```

### Layer 4: Evidence validation

Each final field should have evidence.

```text
If field has value but no evidence → needs_review
If evidence does not contain or imply value → needs_review
```

### Layer 5: Confidence thresholds

Example:

```text
confidence >= 0.90 → auto-approve
0.70 <= confidence < 0.90 → review optional
confidence < 0.70 → human review
```

---

## 14. Retry and Repair Strategy

Do not rerun the full document extraction when one field fails.

Use targeted retry:

```text
field failed validation
→ retrieve more evidence for that field
→ retry only that field
→ validate again
```

Retry examples:

```text
missing invoice_date
→ search for "date", "invoice date", "issued", "billing date"
→ retry extraction from top evidence blocks
```

```text
total_amount parsed as string
→ normalize to number
→ if impossible, retry field extraction with stricter instruction
```

---

## 15. Human Review UI Requirements

Build UI around fields, not only documents.

### Recommended layout

```text
Left panel:
- document list
- page thumbnails
- document type labels

Center panel:
- PDF/page preview
- parsed Markdown/table viewer
- highlighted evidence region

Right panel:
- schema fields
- extracted values
- confidence
- validation errors
- candidate alternatives
- approve/correct buttons
```

### Field card example

```text
Field: invoice_total
Value: 850.00 USD
Status: conflict
Confidence: 0.74
Sources:
  - invoice.pdf page 2: "Total Amount Due: $850.00"
  - receipt.pdf page 1: "Paid: $820.00"
Action: select correct value / edit manually
```

### Reviewer actions

Support:

- approve field
- edit value
- select candidate
- mark missing
- mark not applicable
- add comment
- save correction as training/evaluation data

---

## 16. Evaluation and Benchmarking

Do not measure only final JSON validity. Measure field-level quality.

### Metrics

```text
field precision
field recall
field F1
exact match accuracy
normalized value accuracy
table row accuracy
source citation accuracy
conflict detection accuracy
validation pass rate
human review rate
cost per document
latency per document
LLM tokens per job
```

### Evaluation dataset format

```json
{
  "case_id": "case_001",
  "documents": ["invoice.pdf", "receipt.pdf"],
  "schema_id": "claim_schema_v1",
  "gold": {
    "patient_name": {
      "value": "John Smith",
      "sources": [
        {"document": "claim_form.pdf", "page": 1, "evidence": "Patient Name: John Smith"}
      ]
    }
  }
}
```

### Must-have test cases

Create tests for:

1. Simple one-page invoice.
2. Multi-page invoice with line items.
3. Scanned PDF requiring OCR.
4. Multi-document claim bundle.
5. Same field appearing in two documents with agreement.
6. Same field appearing in two documents with conflict.
7. Missing required field.
8. Wrong type returned by LLM.
9. Table extraction with continuation across pages.
10. Poor parse quality requiring fallback.
11. Very long contract requiring field-level retrieval.
12. User-defined custom schema.
13. Auto-generated schema from examples.

---

## 17. Suggested Implementation Roadmap

### Milestone 1 — Basic upload + parse

Implement:

- file upload endpoint
- document storage
- Docling parser adapter
- PyMuPDF page renderer
- parsed Markdown viewer
- parsed JSON storage

Endpoints:

```text
POST /cases
POST /cases/{case_id}/documents
POST /documents/{document_id}/parse
GET  /documents/{document_id}/parsed
GET  /documents/{document_id}/pages/{page_number}/image
```

### Milestone 2 — Schema CRUD

Implement:

- create schema
- edit schema
- list schemas
- validate schema
- convert JSON Schema to Pydantic-compatible runtime model

Endpoints:

```text
POST /schemas
GET  /schemas
GET  /schemas/{schema_id}
PUT  /schemas/{schema_id}
POST /schemas/{schema_id}/validate
```

### Milestone 3 — Naive extraction baseline

Implement baseline:

```text
parsed Markdown → LLM structured output → Pydantic validation
```

This baseline is not final, but useful for comparison.

Endpoints:

```text
POST /cases/{case_id}/extract-baseline
GET  /extraction-jobs/{job_id}
```

### Milestone 4 — Chunking and indexing

Implement:

- page chunks
- block chunks
- table chunks
- embeddings
- keyword search
- hybrid retrieval

Endpoints:

```text
POST /cases/{case_id}/index
POST /cases/{case_id}/search
```

### Milestone 5 — Field-level extraction

Implement:

```text
for each schema field:
  build retrieval query
  retrieve evidence
  extract candidates
  validate candidates
  store field result
```

Endpoint:

```text
POST /cases/{case_id}/extract
```

### Milestone 6 — Evidence and citations

Implement:

- evidence snippets
- document/page references
- bounding box when available
- PDF highlight support

### Milestone 7 — Conflict detection

Implement:

- multiple candidates per field
- conflict policies
- review-required states

### Milestone 8 — Human review UI

Implement:

- field cards
- evidence viewer
- candidate selector
- correction saving
- final approval

### Milestone 9 — Evaluation harness

Implement:

- gold dataset format
- field-level metrics
- citation accuracy metric
- cost/latency logging
- regression tests

### Milestone 10 — Production hardening

Implement:

- async jobs
- queue worker
- retry logic
- rate limits
- file size limits
- virus scanning if needed
- audit logs
- permissions
- monitoring
- cost dashboard

---

## 18. Suggested Tech Stack

### Backend

- Python
- FastAPI
- Pydantic v2
- SQLModel / SQLAlchemy
- PostgreSQL
- Redis Queue / Celery / Dramatiq / Arq
- Object storage: local filesystem for prototype, S3/GCS/MinIO for production

### Parsing

- Docling as default parser
- PyMuPDF for rendering and coordinates
- pdfplumber for table fallback
- OCR fallback: PaddleOCR / Tesseract / managed OCR

### Retrieval

- PostgreSQL full-text search or Elasticsearch/OpenSearch
- Vector DB: Qdrant / pgvector / Chroma for prototype
- Hybrid retrieval layer

### LLM/VLM

- Use structured output API when available.
- Use cheaper text model for field extraction from clean text.
- Use VLM only for image/region extraction.
- Add model abstraction layer.

### Frontend

- Next.js
- PDF viewer
- Markdown viewer
- JSON editor/viewer
- field review panel
- evidence highlighting

---

## 19. Prompting Guidelines

### Field extraction prompt template

```text
You are extracting one field from a document bundle.

Field path: {field_path}
Field description: {field_description}
Expected type: {field_type}
Allowed values: {enum_if_any}

Use only the provided evidence snippets.
Return candidate values with evidence IDs.
If the evidence is insufficient, return null and explain why.
Do not guess.

Evidence:
{retrieved_evidence}

Return JSON matching this structure:
{
  "candidates": [
    {
      "value": ...,
      "evidence_ids": [...],
      "confidence": 0.0-1.0,
      "reason": "short explanation"
    }
  ]
}
```

### Resolver prompt template

```text
You are resolving candidate values for a schema field.

Field: {field_path}
Description: {field_description}
Conflict policy: {conflict_policy}

Candidates:
{candidates}

Choose the best final value only if evidence is sufficient.
If candidates conflict materially, mark status as conflict.
If no candidate is reliable, mark status as missing or low_confidence.

Return JSON:
{
  "status": "validated | conflict | missing | low_confidence",
  "selected_candidate_id": "... or null",
  "final_value": ...,
  "confidence": 0.0-1.0,
  "reason": "..."
}
```

---

## 20. Cost Control Rules

Avoid context/cost explosion.

Rules:

```text
1. Never send all pages to LLM if document > small threshold.
2. Retrieve top-k evidence per field.
3. Use cheaper models for clean text fields.
4. Use VLM only for selected pages/regions.
5. Cache parse outputs.
6. Cache embeddings.
7. Retry only failed fields.
8. Batch extraction calls when safe.
9. Use deterministic rules for obvious fields.
10. Track tokens and cost per extraction job.
```

Suggested thresholds:

```text
If total parsed tokens <= 8k and document count == 1:
  allow whole-document baseline extraction.
Else:
  use field-level retrieval extraction.
```

---

## 21. Security and Compliance Notes

For production:

- Store original files securely.
- Never expose raw file paths to users.
- Add access control per case/document/job.
- Log all extraction and human review actions.
- Add PII handling policies.
- Support deletion/export of uploaded files.
- Consider on-prem/local parsing for sensitive documents.
- Be careful when sending documents to third-party LLM/OCR APIs.

---

## 22. Important Research Papers and Articles

The coding agent should read these sources and extract implementation ideas from them.

### LlamaIndex / LlamaParse / LlamaExtract docs

1. **LlamaExtract Overview**  
   https://developers.llamaindex.ai/llamaparse/extract/  
   Why read: explains schema-based extraction, SDK workflow, and batch extraction concepts.

2. **LlamaExtract Core Concepts**  
   https://developers.llamaindex.ai/llamaparse/extract/guides/concepts/  
   Why read: concepts such as data schemas, extraction targets, extraction jobs, and extraction runs.

3. **LlamaExtract Configuration Options**  
   https://developers.llamaindex.ai/llamaparse/extract/guides/options/  
   Why read: extraction target and schema application settings.

4. **Auto-Generate Schema for Extraction**  
   https://developers.llamaindex.ai/llamaparse/extract/examples/auto_generate_schema_for_extraction/  
   Why read: useful for user-uploaded examples where schema can be inferred.

5. **Structured Data Extraction in LlamaIndex**  
   https://developers.llamaindex.ai/python/framework/understanding/extraction/  
   Why read: explains Pydantic-centric structured extraction.

6. **Parse vs Extract**  
   https://www.llamaindex.ai/blog/parse-vs-extract  
   Why read: helps separate parsing quality from extraction intelligence.

### Reducto docs

7. **Reducto Extract**  
   https://reducto.ai/extract  
   Why read: product-level schema extraction flow.

8. **Reducto Citations**  
   https://docs.reducto.ai/configs/extract/citations  
   Why read: important for bounding-box citations and traceable extraction outputs.

9. **JSON Schema Extraction with Citations**  
   https://llms.reducto.ai/json-schema-extraction-with-citations  
   Why read: practical pattern for schema extraction plus layout-aware citations.

### Production articles

10. **How Alan Reached 70% Document Processing Automation**  
    https://medium.com/alan/how-we-reached-70-document-processing-automation-at-alan-674bc80f3ef3  
    Why read: real-world move from classic ML to LLM-based extraction with Markdown transcription, classification, and few-shot RAG.

11. **Lessons from Running an LLM Document Processing Pipeline in Production**  
    https://medium.com/alan/lessons-from-running-an-llm-document-processing-pipeline-in-production-33d87f99cdb1  
    Why read: production reliability, evaluation, failure modes, and operational lessons.

12. **How We Built a Configurable Document Processing Pipeline**  
    https://medium.com/alan/how-we-built-a-configurable-document-processing-pipeline-725db34393d6  
    Why read: generalizing a pipeline across countries/use cases.

13. **PyMuPDF: Grounding in Document Extraction**  
    https://pymupdf.io/blog/grounding-in-document-extraction  
    Why read: practical grounding/highlighting approach using PyMuPDF after LLM identification.

14. **Typedef + Reducto OCR + Schema Validation + LLM Fix-ups**  
    https://www.typedef.ai/resources/pair-reducto-ocr-schema-validation-llm-fix-ups-typedef  
    Why read: validation and repair pattern.

15. **Explosion: Human-in-the-loop Distillation**  
    https://explosion.ai/blog/human-in-the-loop-distillation  
    Why read: turning human review into training/evaluation data.

### Research papers

16. **LMDX: Language Model-based Document Information Extraction and Localization**  
    https://arxiv.org/abs/2309.10952  
    Why read: extraction with localization/grounding; crucial for citing where extracted values came from.

17. **BLOCKIE: Information Extraction from Visually Rich Documents using Semantic Blocks**  
    https://arxiv.org/abs/2505.13535  
    Why read: semantic block approach avoids whole-document long-context extraction.

18. **MADP: A Multi-Agent Pipeline for Sustainable Document Processing with Human-in-the-Loop**  
    https://arxiv.org/abs/2605.17159  
    Why read: recent production-style multi-agent pipeline with classifier, splitter, parser, extraction, validator, and HITL.

19. **Guardian Parser Pack: LLM-based Schema-Guided Extraction and Validation of Heterogeneous Documents**  
    https://arxiv.org/abs/2604.06571  
    Why read: schema-first, dual deterministic/LLM pathway, validation, heterogeneous source handling.

20. **ChatSchema: Schema-based Extraction with Large Multimodal Models**  
    https://arxiv.org/abs/2407.18716  
    Why read: OCR + multimodal model + schema extraction.

21. **Layout-Aware Information Extraction for Document-Grounded Dialogue**  
    https://arxiv.org/abs/2207.06717  
    Why read: layout-aware extraction and document-grounded reasoning.

22. **Diagnosing Structural Failures in LLM-Based Evidence Extraction for Meta-Analysis**  
    https://arxiv.org/abs/2602.10881  
    Why read: warns against long-context multi-document extraction failures.

23. **ParseBench: A Document Parsing Benchmark for AI Agents**  
    https://arxiv.org/abs/2604.08538  
    Why read: benchmark focused on semantic correctness, tables, charts, formatting, and visual grounding for agentic document parsing.

24. **PARSE: LLM Driven Schema Optimization for Reliable Entity Extraction**  
    https://arxiv.org/abs/2510.08623  
    Why read: schema optimization and extraction reliability.

25. **Towards End-to-End Information Extraction from Visually Rich Documents**  
    https://arxiv.org/abs/2207.06744  
    Why read: connects text reading and information extraction with multimodal context.

### Parser and OCR projects to inspect

26. **Docling**  
    https://github.com/docling-project/docling  
    Why inspect: parser/converter architecture for PDFs and office documents.

27. **PyMuPDF**  
    https://pymupdf.readthedocs.io/  
    Why inspect: page rendering, blocks, coordinates, highlighting.

28. **pdfplumber**  
    https://github.com/jsvine/pdfplumber  
    Why inspect: table and coordinate extraction.

29. **Marker**  
    https://github.com/datalab-to/marker  
    Why inspect: PDF to Markdown pipeline.

30. **MinerU**  
    https://github.com/opendatalab/MinerU  
    Why inspect: document-to-Markdown/JSON extraction for LLM/RAG.

31. **PaddleOCR**  
    https://github.com/PaddlePaddle/PaddleOCR  
    Why inspect: OCR, table recognition, document structure pipelines.

32. **LayoutParser**  
    https://github.com/Layout-Parser/layout-parser  
    Why inspect: document layout analysis.

---

## 23. Initial Coding Tasks for the Agent

Start with these concrete tasks.

### Task 1 — Create project modules

Create this backend structure:

```text
app/
  main.py
  api/
    cases.py
    documents.py
    schemas.py
    extraction.py
    review.py
  core/
    config.py
    storage.py
    logging.py
  parsers/
    base.py
    docling_parser.py
    pymupdf_parser.py
    pdfplumber_parser.py
  extraction/
    planner.py
    retriever.py
    field_extractor.py
    candidate_resolver.py
    validator.py
    prompts.py
  models/
    document.py
    schema.py
    extraction.py
    review.py
  services/
    indexing_service.py
    parsing_service.py
    extraction_service.py
    review_service.py
  tests/
```

### Task 2 — Define Pydantic models

Implement:

- `ParsedDocument`
- `ParsedPage`
- `TextBlock`
- `ParsedTable`
- `EvidenceSource`
- `ExtractionSchema`
- `ExtractionJob`
- `FieldCandidate`
- `FieldResult`
- `ExtractionResult`

### Task 3 — Implement parser adapters

Implement:

- `BaseParser`
- `DoclingParser`
- `PyMuPDFParser`
- optional `PdfPlumberTableParser`

Return normalized `ParsedDocument`.

### Task 4 — Implement baseline extraction

Input:

```text
case_id + schema_id
```

Process:

```text
combine parsed Markdown from small docs
→ call LLM structured output
→ validate
→ save result
```

This is the baseline to beat.

### Task 5 — Implement field-level extraction

For each schema field:

1. Build retrieval query from field metadata.
2. Retrieve relevant blocks/tables.
3. Call LLM for field candidates.
4. Validate candidates.
5. Resolve final field result.
6. Save evidence.

### Task 6 — Implement review UI APIs

APIs:

```text
GET  /extraction-jobs/{job_id}/review
POST /extraction-jobs/{job_id}/fields/{field_path}/approve
POST /extraction-jobs/{job_id}/fields/{field_path}/correct
POST /extraction-jobs/{job_id}/finalize
```

### Task 7 — Implement export

Exports:

```text
final.json
parsed_markdown.md
evidence_report.json
validation_report.json
review_log.json
```

---

## 24. Definition of Done

The first production-like MVP is done when:

1. User can create a case.
2. User can upload multiple documents.
3. System parses documents into Markdown/pages/blocks/tables.
4. User can create/select a schema.
5. System runs field-level extraction.
6. Each field has value, status, confidence, and evidence.
7. Conflicting field values are detected.
8. Human reviewer can approve/correct fields.
9. Final JSON can be downloaded.
10. Evaluation script can compare result against a small gold dataset.

---

## 25. Non-Goals for MVP

Do not start with:

- full custom model training
- perfect OCR for all languages
- perfect table extraction
- complex no-code schema builder
- enterprise SSO
- full workflow automation
- fine-tuning
- multi-tenant billing

Start with:

```text
reliable parsing
field-level extraction
evidence citations
validation
human review
export
```

---

## 26. Final Architecture Summary

Build this system around one core idea:

```text
Parsing creates searchable evidence.
Extraction is schema-aware field-level evidence selection.
Validation decides whether extracted values are safe to use.
Human review corrects uncertain values and creates future training/evaluation data.
```

The final JSON should not be naked data. It should be grounded data:

```json
{
  "invoice_total": {
    "value": 850.0,
    "status": "validated",
    "confidence": 0.94,
    "sources": [
      {
        "document": "invoice.pdf",
        "page": 2,
        "evidence": "Total Amount Due: $850.00",
        "bbox": [412.2, 690.0, 552.8, 718.3]
      }
    ]
  }
}
```

That is the main difference between a toy parser and a production-ready document extraction platform.
