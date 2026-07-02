# AGENT.md — Context-Window-Safe Production Document Extraction Workflow

**Purpose:**  
This file updates the existing `AGENT.md` document-extraction workflow with a dedicated **context-window management layer**.

The goal is to build a production-grade document extraction platform for **long, noisy, unclean PDFs**, especially documents with:

- many pages
- financial tables
- visual charts
- scanned pages
- OCR noise
- mixed layouts
- multiple uploaded documents per case
- user-defined schemas
- repeated/conflicting fields across documents

This workflow does **not** replace the existing parsing/extraction workflow. It extends it with a **Context Optimization Layer** so the system avoids sending full documents into an LLM/VLM.

---

## 0. Core Problem

The naive extraction approach is:

```text
PDF
→ parse all text
→ send entire document to LLM
→ ask for schema JSON
```

This breaks in production because:

```text
1. Long PDFs exceed context window.
2. Large prompts are expensive.
3. Retrieval noise increases hallucination.
4. Tables/charts are often lost in raw text.
5. Full-document VLM calls are slow and costly.
6. Financial values need source grounding.
7. Multi-document bundles may contain duplicate/conflicting evidence.
8. Whole-document prompting is hard to debug.
```

Therefore, the production approach should be:

```text
Document bundle
→ parse into evidence objects
→ localize relevant pages/regions per schema field
→ build compact evidence packs
→ selectively call text LLM / VLM / table logic
→ validate
→ expand context only on failure
→ human review if still uncertain
```

---

## 1. Key Design Principle

The LLM should not see “the whole document.”

The LLM should see:

```text
the smallest sufficient evidence pack
for one field or one small group of related fields
```

Example:

```text
Bad:
"Here is an 80-page financial report. Extract total revenue, net income, risks, and chart insights."

Good:
"Field: total_revenue
Evidence:
- income statement table, page 44
- financial highlights block, page 8
- revenue chart crop, page 9

Extract only total_revenue with evidence."
```

---

## 2. Production Pattern from Recent Research and Products

Recent production-style document extraction systems are converging around this pattern:

```text
1. Image/page preprocessing
2. OCR and layout parsing
3. Page-level or element-level localization
4. Field-specific retrieval
5. Selective multimodal reasoning
6. Schema validation
7. Citations / grounding
8. Human review
```

Important sources:

- A 2026 industrial KYC paper on long scanned financial documents found that direct full-document VLM extraction is unreliable. Their better approach separates **page localization** from **multimodal reasoning**, using image preprocessing, OCR, hybrid page retrieval, and compact VLM extraction. Page-level retrieval was the dominant factor in performance gains.  
  https://arxiv.org/abs/2604.26462

- A 2025 financial-document extraction paper proposes a multistage pipeline with image processing, OCR, retrieval, and compact VLM extraction instead of direct whole-document VLM prompting.  
  https://arxiv.org/html/2510.23066v1

- ParseBench argues that agent-ready document parsing must evaluate tables, charts, content faithfulness, semantic formatting, and visual grounding, not just text similarity.  
  https://arxiv.org/abs/2604.08538  
  https://www.parsebench.ai/

- MPDocBench-Parse emphasizes realistic multi-page parsing problems including semantic continuity, hierarchical structure, visual content preservation, table merging, figures, reading order, and heading hierarchy.  
  https://arxiv.org/abs/2605.22100

- LlamaExtract emphasizes custom schemas, confidence scores, granular citations, and reasoning for extracted fields.  
  https://www.llamaindex.ai/llamaextract  
  https://www.llamaindex.ai/blog/get-citations-and-reasoning-for-extracted-data-in-llamaextract

- Firecrawl is for web data, not PDF, but its architecture is useful: turn messy sources into clean Markdown/structured data that agents can use.  
  https://github.com/firecrawl/firecrawl  
  https://www.firecrawl.dev/

---

## 3. Existing Workflow Foundation

The base workflow remains:

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

The update in this file adds a new middle layer:

```text
Context Optimization Layer
```

This layer controls:

```text
what evidence enters the model context,
what stays in storage,
when to use VLM,
when to retry with more evidence,
and when to send a field to human review.
```

---

## 4. Updated High-Level Architecture

```text
                         ┌────────────────────┐
                         │ User Upload Bundle  │
                         │ PDFs / scans / docs │
                         └─────────┬──────────┘
                                   │
                         ┌─────────▼──────────┐
                         │ Ingestion Service   │
                         │ case_id, doc_id     │
                         └─────────┬──────────┘
                                   │
              ┌────────────────────▼────────────────────┐
              │ Page Rendering + Preprocessing           │
              │ PyMuPDF, OpenCV, orientation, cleanup    │
              └────────────────────┬────────────────────┘
                                   │
        ┌──────────────────────────▼──────────────────────────┐
        │ Multi-Parser Layer                                   │
        │ Docling / OCR / pdfplumber / table parser / layout   │
        └──────────────────────────┬──────────────────────────┘
                                   │
       ┌───────────────────────────▼───────────────────────────┐
       │ Evidence Store                                         │
       │ pages, blocks, tables, chart crops, OCR tokens, bboxes │
       └───────────────────────────┬───────────────────────────┘
                                   │
              ┌────────────────────▼────────────────────┐
              │ Hybrid Index                             │
              │ keyword + vector + layout + table index  │
              └────────────────────┬────────────────────┘
                                   │
                         ┌─────────▼──────────┐
                         │ User Schema         │
                         │ Pydantic / JSON     │
                         └─────────┬──────────┘
                                   │
              ┌────────────────────▼────────────────────┐
              │ Schema-Aware Extraction Planner          │
              │ field → evidence type → retrieval plan   │
              └────────────────────┬────────────────────┘
                                   │
              ┌────────────────────▼────────────────────┐
              │ Context Optimization Layer               │
              │ budget, evidence packs, routing, retry   │
              └────────────────────┬────────────────────┘
                                   │
        ┌──────────────────────────▼──────────────────────────┐
        │ Field-Level Extraction                              │
        │ rules + tables + text LLM + selected-region VLM     │
        └──────────────────────────┬──────────────────────────┘
                                   │
              ┌────────────────────▼────────────────────┐
              │ Candidate Resolution + Validation        │
              │ Pydantic + business rules + conflicts    │
              └────────────────────┬────────────────────┘
                                   │
        ┌──────────────────────────▼──────────────────────────┐
        │ Review UI + Final JSON                              │
        │ citations, bboxes, confidence, downloadable output   │
        └─────────────────────────────────────────────────────┘
```

---

## 5. New Layer: Context Optimization Layer

Create these modules:

```text
app/extraction/context_budget.py
app/extraction/evidence_pack.py
app/extraction/progressive_retrieval.py
app/extraction/visual_router.py
app/extraction/cost_tracker.py
app/extraction/field_memory.py
```

### Responsibilities

```text
1. Estimate token cost per evidence item.
2. Estimate VLM image cost per region.
3. Select the smallest sufficient evidence per field.
4. Prefer exact evidence over summaries.
5. Prefer tables over flattened text for financial values.
6. Use VLM only when visual reasoning is required.
7. Retry failed fields with expanded evidence.
8. Track model cost, tokens, latency, and failure modes.
```

---

## 6. Evidence Pack Concept

An **Evidence Pack** is a compact, field-specific context object.

It is the only thing that should be passed to the LLM/VLM for extraction.

### EvidencePack model

```python
from pydantic import BaseModel
from typing import Literal, Any

class EvidenceSnippet(BaseModel):
    evidence_id: str
    document_id: str
    filename: str
    page_number: int
    source_type: Literal[
        "text_block",
        "table",
        "table_row",
        "table_cell",
        "chart",
        "figure",
        "page_summary",
        "image_region",
        "ocr_line"
    ]
    text: str | None = None
    markdown: str | None = None
    bbox: list[float] | None = None
    confidence: float | None = None

class ImageEvidence(BaseModel):
    evidence_id: str
    document_id: str
    filename: str
    page_number: int
    image_path: str
    crop_bbox: list[float] | None = None
    caption: str | None = None
    nearby_text: str | None = None

class EvidencePack(BaseModel):
    field_path: str
    field_description: str
    expected_type: str
    text_snippets: list[EvidenceSnippet] = []
    tables: list[EvidenceSnippet] = []
    chart_crops: list[ImageEvidence] = []
    page_refs: list[str] = []
    estimated_text_tokens: int
    estimated_image_count: int
    retrieval_reason: str | None = None
```

### Example EvidencePack

```json
{
  "field_path": "total_revenue",
  "field_description": "Total revenue or net sales for the reporting period.",
  "expected_type": "number",
  "text_snippets": [
    {
      "evidence_id": "block_44_3",
      "document_id": "doc_001",
      "filename": "annual_report.pdf",
      "page_number": 44,
      "source_type": "text_block",
      "text": "Total revenue for 2025 was USD 12.8 million."
    }
  ],
  "tables": [
    {
      "evidence_id": "table_44_1",
      "document_id": "doc_001",
      "filename": "annual_report.pdf",
      "page_number": 44,
      "source_type": "table",
      "markdown": "| Year | Revenue | Net Income |\n|---|---:|---:|\n| 2025 | 12.8M | 1.9M |"
    }
  ],
  "chart_crops": [],
  "estimated_text_tokens": 900,
  "estimated_image_count": 0
}
```

---

## 7. Context Budget Manager

### Goal

Control how much evidence can be passed to each model call.

### ContextBudget model

```python
class ContextBudget(BaseModel):
    max_text_tokens: int = 6000
    max_images: int = 2
    max_tables: int = 5
    max_pages: int = 5
    max_evidence_items: int = 12
    model_name: str
    allow_neighbor_blocks: bool = True
```

### Context budget policy

```text
Small field:
  max 2,000 tokens
  max 0–1 images

Numeric financial field:
  max 4,000 tokens
  prioritize tables and exact snippets

Chart field:
  max 3,000 text tokens
  max 1–2 chart crops

Long text field:
  max 6,000–10,000 tokens
  use section retrieval and summarization

Nested/list field:
  max 8,000–12,000 tokens
  split into subfields if possible
```

### Budget selection pseudo-code

```python
def build_context_pack(field, evidence_candidates, budget: ContextBudget) -> EvidencePack:
    ranked = rank_evidence_for_field(field, evidence_candidates)

    pack = EvidencePack(
        field_path=field.path,
        field_description=field.description,
        expected_type=field.type,
        estimated_text_tokens=0,
        estimated_image_count=0,
    )

    for item in ranked:
        compact = compact_evidence(item)

        if compact.source_type in ["chart", "image_region"]:
            if pack.estimated_image_count >= budget.max_images:
                continue
            pack.chart_crops.append(compact)
            pack.estimated_image_count += 1
            continue

        if pack.estimated_text_tokens + compact.estimated_tokens > budget.max_text_tokens:
            continue

        if compact.source_type == "table":
            if len(pack.tables) >= budget.max_tables:
                continue
            pack.tables.append(compact)
        else:
            pack.text_snippets.append(compact)

        pack.estimated_text_tokens += compact.estimated_tokens

        if total_evidence_items(pack) >= budget.max_evidence_items:
            break

    return pack
```

---

## 8. Progressive Retrieval Policy

Do not start with maximum context. Start small and expand only when necessary.

### Progressive stages

```text
Attempt 1:
  top 3 evidence items
  no neighboring blocks
  no VLM unless field is explicitly visual

Attempt 2:
  top 8 evidence items
  include neighboring blocks
  include table rows/captions

Attempt 3:
  retrieve page-level context
  include page image or cropped region
  route to VLM

Attempt 4:
  mark field as needs_review
```

### Pseudo-code

```python
def extract_field_with_progressive_context(field, case_id):
    attempts = [
        RetrievalPolicy(k=3, include_neighbors=False, allow_visual=False),
        RetrievalPolicy(k=8, include_neighbors=True, allow_visual=False),
        RetrievalPolicy(k=12, include_neighbors=True, allow_visual=True),
    ]

    for attempt_no, policy in enumerate(attempts, start=1):
        evidence = retrieve_evidence(field, case_id, policy)
        pack = build_context_pack(field, evidence, policy.budget)

        result = run_extraction(field, pack)
        validation = validate_field_result(field, result)

        save_attempt(field, attempt_no, pack, result, validation)

        if validation.passed:
            return result

    return mark_needs_review(field)
```

---

## 9. Summary-As-Router, Not Summary-As-Evidence

Summaries can reduce search cost, but they should not be the final source for exact extraction.

### Use summaries for:

```text
- page routing
- section routing
- deciding which pages to inspect
- detecting page purpose
- quick document navigation
```

### Do not use summaries for:

```text
- final numeric values
- final dates
- legal clause wording
- audited financial figures
- exact table values
```

### Safe pattern

```text
Page summaries
→ identify likely page/table/chart
→ retrieve exact table/snippet/crop
→ extract final field from exact evidence
```

---

## 10. Multi-Granularity Index

Do not index only chunks. Index document elements.

### Evidence unit types

```text
1. page_summary
2. page_full_text
3. text_block
4. section
5. table
6. table_row
7. table_cell
8. chart_region
9. figure_region
10. OCR_line
11. heading
12. footnote
```

### Recommended indexes

```text
Keyword index:
  Elasticsearch / OpenSearch / PostgreSQL FTS

Vector index:
  Qdrant / pgvector / FAISS / Chroma

Table index:
  rows, columns, captions, page, bbox

Visual index:
  chart crops, figure crops, screenshots, captions, nearby text

Metadata index:
  document type, page type, parser quality, OCR confidence
```

### Element model

```python
class EvidenceElement(BaseModel):
    element_id: str
    case_id: str
    document_id: str
    page_number: int
    element_type: Literal[
        "page_summary",
        "page_text",
        "text_block",
        "table",
        "table_row",
        "chart",
        "figure",
        "ocr_line",
        "heading",
        "footnote"
    ]
    text: str | None = None
    markdown: str | None = None
    image_path: str | None = None
    bbox: list[float] | None = None
    embedding_id: str | None = None
    parser_confidence: float | None = None
    metadata: dict[str, Any] = {}
```

---

## 11. Schema-Aware Retrieval

For each schema field, the system should infer:

```text
1. expected evidence type
2. likely document type
3. likely page/section
4. likely keywords
5. whether table extraction is needed
6. whether visual/VLM extraction is needed
7. how much context is allowed
```

### Example schema field

```python
class FinancialExtraction(BaseModel):
    total_revenue: float = Field(
        description="Total revenue or net sales for the current reporting period. Usually appears in the income statement, financial highlights table, or revenue chart."
    )
```

### Derived retrieval plan

```json
{
  "field_path": "total_revenue",
  "expected_evidence_types": ["table", "table_row", "text_block", "chart"],
  "keywords": [
    "total revenue",
    "net sales",
    "revenue",
    "income statement",
    "financial highlights"
  ],
  "preferred_pages": ["income statement", "financial highlights", "management discussion"],
  "visual_allowed": true,
  "max_text_tokens": 4000,
  "max_images": 1
}
```

---

## 12. Visual Routing Policy

Use VLM only when useful.

### Route to text LLM / rules when:

```text
- evidence is clean text
- table is already parsed correctly
- field is simple string/date/number
- OCR confidence is high
```

### Route to VLM when:

```text
- field is inside a chart
- table parser failed
- OCR confidence is poor
- page is scanned/noisy
- visual layout determines meaning
- checkbox/signature/stamp/image matters
- text extraction and validation failed
```

### VisualRouter pseudo-code

```python
def should_use_vlm(field, evidence_pack):
    if field.metadata.get("requires_visual_reasoning"):
        return True

    if evidence_pack.chart_crops:
        return True

    if any(e.confidence is not None and e.confidence < 0.70 for e in evidence_pack.text_snippets):
        return True

    if field.last_validation_error in ["missing", "ambiguous_table", "conflict"]:
        return True

    return False
```

---

## 13. Financial Document-Specific Strategy

For noisy financial PDFs, use these rules.

### Financial fields should prefer source hierarchy

```text
Highest trust:
  audited financial statement tables

Medium trust:
  management discussion text
  financial highlights tables

Lower trust:
  charts without exact labels
  investor presentation visuals
  marketing summary pages
```

### Example source preference

```python
SOURCE_PRIORITY = {
    "audited_income_statement": 1.0,
    "cash_flow_statement": 1.0,
    "balance_sheet": 1.0,
    "notes_to_financials": 0.9,
    "financial_highlights": 0.8,
    "management_discussion": 0.7,
    "chart": 0.6,
    "presentation_summary": 0.5,
}
```

### Financial validation examples

```text
gross_profit ≈ revenue - cost_of_revenue
operating_profit ≈ gross_profit - operating_expenses
cash_end ≈ cash_begin + net_change_in_cash
total_assets ≈ total_liabilities + total_equity
```

### Chart caution

Chart extraction should mark values as estimated unless exact labels are present.

```json
{
  "value": 12.8,
  "unit": "USD million",
  "exactness": "estimated_from_chart",
  "needs_review": true
}
```

---

## 14. Table-First Extraction

Financial documents are table-heavy, so tables should not be flattened too early.

### Bad

```text
PDF table
→ raw text
→ LLM guesses value
```

### Good

```text
PDF table
→ structured rows/columns
→ row/column identification
→ numeric normalization
→ formula validation
→ LLM only for ambiguity
```

### TableEvidence model

```python
class TableEvidence(BaseModel):
    table_id: str
    document_id: str
    filename: str
    page_number: int
    caption: str | None = None
    bbox: list[float] | None = None
    columns: list[str]
    rows: list[dict]
    markdown: str
    parser_name: str
    confidence: float | None = None
```

### Table extraction flow

```text
1. Detect table.
2. Extract rows/columns.
3. Normalize numbers.
4. Identify row label relevant to field.
5. Identify current reporting period column.
6. Extract cell.
7. Validate.
8. Attach citation to table cell or row.
```

---

## 15. Chart-Aware Extraction

For financial visual charts:

```text
1. Detect chart region.
2. Crop chart image.
3. Extract chart title, axis labels, legend, data labels.
4. Retrieve nearby caption/paragraph.
5. Send crop + context to VLM.
6. Extract structured chart JSON.
7. Mark exactness level.
8. Validate against nearby text/table when possible.
```

### ChartExtraction schema

```python
class ChartExtraction(BaseModel):
    chart_type: str | None
    title: str | None
    x_axis: str | None
    y_axis: str | None
    unit: str | None
    series: list[dict]
    exactness: Literal["exact_labels_present", "estimated_from_visual", "unknown"]
    confidence: float
    source_page: int
    bbox: list[float] | None
```

---

## 16. Candidate Memory

Every field extraction should store all attempts.

### Why?

So the system can debug:

```text
- which context was used
- why first attempt failed
- what evidence was added
- why final value was selected
- why field was sent to review
```

### FieldAttempt model

```python
class FieldAttempt(BaseModel):
    attempt_id: str
    job_id: str
    field_path: str
    attempt_number: int
    retrieval_policy: dict
    evidence_pack: EvidencePack
    model_name: str
    output_json: dict | None
    validation_status: str
    validation_errors: list[str]
    token_count: int
    image_count: int
    latency_ms: int
    cost_usd: float | None = None
```

---

## 17. Cost Tracking

Track costs per:

```text
- document
- page
- field
- extraction job
- model
- parser
- retry attempt
```

### Cost metrics

```python
class ExtractionCost(BaseModel):
    job_id: str
    field_path: str | None = None
    model_name: str
    input_tokens: int
    output_tokens: int
    image_count: int = 0
    latency_ms: int
    estimated_cost_usd: float | None = None
```

### Cost dashboard should show

```text
average cost per document
average cost per field
highest-cost fields
VLM usage percentage
retry rate
human review rate
tokens saved by context packing
```

---

## 18. Updated Extraction Algorithm

```python
def run_context_safe_extraction(case_id: str, schema_id: str):
    # 1. Load parsed evidence
    evidence_store = load_evidence_store(case_id)

    # 2. Load schema
    schema = load_schema(schema_id)

    # 3. Build field plans
    field_plans = []
    for field in schema.fields:
        plan = build_schema_aware_retrieval_plan(field)
        field_plans.append(plan)

    final_results = {}

    # 4. Extract field-by-field
    for plan in field_plans:
        result = extract_field_context_safe(
            case_id=case_id,
            field_plan=plan,
            evidence_store=evidence_store,
        )
        final_results[plan.field_path] = result

    # 5. Resolve cross-field and cross-document conflicts
    resolved = resolve_conflicts(final_results)

    # 6. Validate final object
    validation = validate_final_schema(resolved, schema)

    # 7. Decide review status
    if validation.has_errors or has_low_confidence(resolved):
        mark_job_needs_review()
    else:
        mark_job_completed()

    return build_final_extraction_result(resolved, validation)
```

### Field extraction

```python
def extract_field_context_safe(case_id, field_plan, evidence_store):
    policies = [
        RetrievalPolicy(name="small", k=3, include_neighbors=False, allow_visual=False),
        RetrievalPolicy(name="medium", k=8, include_neighbors=True, allow_visual=False),
        RetrievalPolicy(name="visual", k=12, include_neighbors=True, allow_visual=True),
    ]

    for policy in policies:
        candidates = retrieve_evidence(
            case_id=case_id,
            field_plan=field_plan,
            policy=policy,
            evidence_store=evidence_store,
        )

        pack = build_evidence_pack(
            field_plan=field_plan,
            candidates=candidates,
            budget=policy.budget,
        )

        if should_use_vlm(field_plan, pack):
            output = run_vlm_extraction(field_plan, pack)
        else:
            output = run_text_or_table_extraction(field_plan, pack)

        validation = validate_field_output(field_plan, output)

        save_field_attempt(field_plan, policy, pack, output, validation)

        if validation.passed:
            return output

    return make_review_required_result(field_plan)
```

---

## 19. Prompt Templates

### Field extraction prompt

```text
You are extracting one schema field from a document bundle.

Field path:
{field_path}

Field description:
{field_description}

Expected type:
{expected_type}

Rules:
- Use only the provided evidence.
- Do not guess.
- Return null if evidence is insufficient.
- Attach evidence IDs to every candidate.
- Prefer exact table/snippet evidence over summaries.
- If values conflict, return all candidates.

Evidence pack:
{evidence_pack}

Return JSON:
{
  "candidates": [
    {
      "value": "...",
      "normalized_value": "...",
      "evidence_ids": ["..."],
      "confidence": 0.0,
      "reason": "short reason"
    }
  ],
  "status": "found | missing | conflict | low_confidence"
}
```

### Resolver prompt

```text
You are resolving candidate values for one schema field.

Field:
{field_path}

Description:
{field_description}

Conflict policy:
{conflict_policy}

Candidates:
{candidates}

Choose a final value only when evidence is sufficient.
If candidates materially conflict, mark conflict.
If evidence is weak, mark low_confidence.
If missing, mark missing.

Return JSON:
{
  "status": "validated | conflict | missing | low_confidence",
  "selected_candidate_id": "... or null",
  "final_value": "... or null",
  "confidence": 0.0,
  "reason": "..."
}
```

### Chart VLM prompt

```text
You are extracting structured data from a financial chart region.

Use the image crop and nearby text only.
Do not invent exact numeric values if labels are not visible.
If values are visually estimated, mark exactness = "estimated_from_visual".

Expected output:
{
  "chart_type": "...",
  "title": "...",
  "x_axis": "...",
  "y_axis": "...",
  "unit": "...",
  "series": [...],
  "exactness": "exact_labels_present | estimated_from_visual | unknown",
  "confidence": 0.0
}
```

---

## 20. How This Avoids Overlap With PM Workflow

The uploaded base workflow already covers:

```text
- parsing
- schema-aware extraction
- field-level retrieval
- validation
- evidence/citations
- human review
```

This update adds:

```text
- context budgeting
- evidence pack construction
- progressive retrieval
- selective VLM routing
- summary-as-router policy
- retry-on-failure context expansion
- cost/token/latency tracking
```

So this is a **production-hardening layer**, not a replacement.

Suggested framing:

```text
The existing workflow defines how extraction should work.
This extension defines how much context each extraction step is allowed to use,
how to select that context, and when to expand or route to VLM.
```

---

## 21. Implementation Roadmap

### Milestone 1 — Evidence model and storage

Implement:

```text
EvidenceElement
EvidenceSnippet
ImageEvidence
EvidencePack
```

Store page/block/table/chart evidence with:

```text
document_id
page_number
bbox
source_type
parser_confidence
text/markdown/image path
```

### Milestone 2 — Token and image budget estimator

Implement:

```text
estimate_tokens(text)
estimate_table_tokens(markdown)
estimate_pack_cost(pack, model)
```

### Milestone 3 — Schema-aware retrieval planner

For each schema field:

```text
field name
description
type
keywords
expected evidence types
visual requirement
budget
```

### Milestone 4 — Evidence pack builder

Build compact field-specific context packs.

### Milestone 5 — Progressive retrieval

Add:

```text
small → medium → visual → review
```

### Milestone 6 — Visual router

Route only selected fields/evidence packs to VLM.

### Milestone 7 — Candidate memory

Store all attempts and validation errors.

### Milestone 8 — Cost dashboard

Track token, image, latency, and model cost.

### Milestone 9 — Financial document extensions

Add:

```text
table-first extraction
chart extraction
financial source priority
formula validation
```

---

## 22. Suggested Project Structure

```text
app/
  extraction/
    context_budget.py
    evidence_pack.py
    progressive_retrieval.py
    visual_router.py
    cost_tracker.py
    field_memory.py
    planner.py
    retriever.py
    field_extractor.py
    candidate_resolver.py
    validator.py
    prompts.py

  models/
    evidence.py
    context.py
    extraction.py
    schema.py
    review.py

  parsers/
    docling_parser.py
    pymupdf_parser.py
    pdfplumber_parser.py
    paddleocr_parser.py
    chart_detector.py
    table_parser.py

  services/
    parsing_service.py
    indexing_service.py
    extraction_service.py
    context_service.py
    review_service.py
    cost_service.py

  api/
    cases.py
    documents.py
    schemas.py
    extraction.py
    review.py
    costs.py
```

---

## 23. Evaluation Metrics

Measure whether the context optimization layer actually helps.

### Accuracy metrics

```text
field exact match
normalized numeric accuracy
date accuracy
table cell accuracy
chart value accuracy
conflict detection accuracy
source citation accuracy
bbox localization accuracy
```

### Context metrics

```text
average input tokens per field
average input tokens per document
average images per field
VLM call rate
retry rate
context expansion rate
tokens saved compared to whole-document baseline
```

### Cost/latency metrics

```text
cost per document
cost per field
latency per document
latency per field
cost by model
cost by strategy
```

### Human review metrics

```text
review rate
correction rate
most failed fields
most expensive fields
most ambiguous document types
```

---

## 24. Test Cases

Create tests for:

```text
1. 2-page clean invoice.
2. 80-page annual report.
3. Noisy scanned financial report.
4. Financial report with visual charts.
5. Multi-document financial bundle.
6. Same field appearing in multiple documents with agreement.
7. Same field appearing in multiple documents with conflict.
8. Table with current/prior year columns.
9. Chart with exact labels.
10. Chart without exact labels.
11. OCR-poor scanned table.
12. Missing required field.
13. Field that requires page-level expansion.
14. Field that requires VLM fallback.
15. Long field extraction where summary is okay for routing but not final evidence.
```

---

## 25. Definition of Done

The updated workflow is implemented when:

```text
1. System can parse long PDF bundles.
2. System can index pages, blocks, tables, and chart regions.
3. Each schema field creates a retrieval plan.
4. Each field gets a compact evidence pack.
5. The LLM/VLM never receives the full document by default.
6. Context expands only after validation failure.
7. VLM is used selectively.
8. Every final field has citation/evidence.
9. Conflicts are detected and reviewable.
10. Costs/tokens/latency are tracked per field.
11. Evaluation compares against whole-document baseline.
```

---

## 26. Final Summary

The updated production workflow should be:

```text
Parse documents into evidence.
Index evidence at multiple granularities.
Use schema fields to retrieve only relevant evidence.
Build compact evidence packs.
Extract field-by-field.
Use VLM only for visual/noisy evidence.
Validate every value.
Expand context only when needed.
Show evidence and conflicts to humans.
Track cost, tokens, and accuracy.
```

The central principle:

```text
Do not optimize only the parser.
Optimize the context passed into each extraction decision.
```

That is how the system handles long context windows without duplicating the existing PM workflow.

---

## 27. Reading List

### Research papers

1. **A Multistage Extraction Pipeline for Long Scanned Financial Documents: An Empirical Study in Industrial KYC Workflows**  
   https://arxiv.org/abs/2604.26462  
   Focus: long scanned financial docs, OCR, hybrid page retrieval, compact VLM extraction.

2. **Multi-Stage Field Extraction of Financial Documents with Traditional CV/OCR and Compact VLMs**  
   https://arxiv.org/html/2510.23066v1  
   Focus: financial-document extraction with preprocessing, OCR, retrieval, compact VLMs.

3. **ParseBench: A Document Parsing Benchmark for AI Agents**  
   https://arxiv.org/abs/2604.08538  
   Focus: tables, charts, content faithfulness, semantic formatting, visual grounding.

4. **MPDocBench-Parse: Benchmarking Practical Multi-page Document Parsing**  
   https://arxiv.org/abs/2605.22100  
   Focus: realistic multi-page parsing, hierarchy, continuity, visual content.

5. **Dr. DocBench: A Comprehensive Benchmark for Expert-Level and Difficult Document Parsing**  
   https://arxiv.org/abs/2606.01393  
   Focus: difficult long-document parsing and parser-failure-based benchmark selection.

### Product/platform docs and articles

6. **LlamaExtract**  
   https://www.llamaindex.ai/llamaextract  
   Focus: schema extraction, confidence scores, citations.

7. **LlamaExtract citations and reasoning**  
   https://www.llamaindex.ai/blog/get-citations-and-reasoning-for-extracted-data-in-llamaextract  
   Focus: field-level citations and reasoning.

8. **ParseBench site**  
   https://www.parsebench.ai/  
   Focus: benchmark dimensions and leaderboard.

9. **Firecrawl GitHub**  
   https://github.com/firecrawl/firecrawl  
   Focus: clean Markdown/structured data for agents.

10. **Firecrawl product site**  
    https://www.firecrawl.dev/  
    Focus: turning messy web sources into LLM-ready data.

11. **PyMuPDF Grounding in Document Extraction**  
    https://pymupdf.io/blog/grounding-in-document-extraction  
    Focus: grounding extracted values to PDF source locations.

12. **Virtido Document Intelligence with LLMs**  
    https://virtido.com/blog/document-intelligence-llm-extraction-guide  
    Focus: production document intelligence pipeline stages.

---

## 28. Immediate Coding Tasks

Give these tasks to the coding agent first:

```text
Task 1:
Create EvidenceElement, EvidencePack, ContextBudget, FieldAttempt models.

Task 2:
Add token estimator and context budget manager.

Task 3:
Extend current retrieval to return evidence items with source_type, page, bbox, confidence.

Task 4:
Implement field-level EvidencePack builder.

Task 5:
Implement progressive retrieval:
small → medium → visual → review.

Task 6:
Implement VisualRouter.

Task 7:
Log attempts, tokens, image count, latency, and validation errors.

Task 8:
Add evaluation comparing:
whole-document baseline vs context-safe field extraction.

Task 9:
Add financial table-first extraction strategy.

Task 10:
Add chart-region extraction interface for VLM fallback.
```

---

## 29. Non-Goals

Do not start with:

```text
- custom VLM fine-tuning
- perfect chart extraction
- full no-code schema builder
- enterprise workflow automation
- expensive VLM on every page
- replacing the existing PM workflow
```

Start with:

```text
- evidence packs
- context budgeting
- field-level retrieval
- selective VLM routing
- validation-driven retry
- cost tracking
```

---

## 30. Final Instruction to Coding Agent

Build this as an extension to the existing workflow.

Do not remove the existing parsing, schema extraction, validation, and review system.

Add a new layer that answers:

```text
For each schema field, what is the minimum reliable evidence needed,
and which model should receive it?
```

The final output must always include:

```text
value
status
confidence
source document
page number
evidence text or crop
bbox when available
validation status
review requirement
cost/token metadata
```
