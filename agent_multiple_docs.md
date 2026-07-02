Yes — this is exactly where a document extraction system becomes a **distributed batch-processing system**, not just an AI pipeline.

For hundreds of documents, production systems do **not** do this:

```text
request comes in
→ parse 300 documents synchronously
→ wait overnight
→ extract after everything is parsed
→ return response
```

Instead, they do:

```text
upload documents
→ create long-running job
→ fan out parsing work
→ save partial parse outputs
→ trigger extraction incrementally when enough evidence is ready
→ track progress per document/page/field
→ allow retries and human review
```

The user should see a job dashboard, not a blocking request.

---

## 1. The core production pattern

The system should separate the pipeline into **asynchronous stages**:

```text
Stage 1: Ingestion
Stage 2: Parsing/OCR/layout extraction
Stage 3: Indexing parsed evidence
Stage 4: Field-level extraction
Stage 5: Validation/conflict detection
Stage 6: Human review/export
```

Each stage writes durable outputs to storage/database.

So extraction does not “wait in memory” for parsing. It waits for **parse artifacts** in storage.

```text
raw PDF
→ parsed pages/blocks/tables saved to DB/object storage
→ extraction job reads those saved artifacts later
```

This is exactly how managed systems treat long-running document jobs. Google Document AI, for example, uses asynchronous batch processing for multiple/larger documents, returns a long-running operation, and writes processed JSON outputs to Cloud Storage instead of holding the HTTP request open. ([Google Cloud Documentation][1])

---

## 2. Think in terms of `case_id`, `document_id`, `page_id`, and `job_id`

You need a durable job model.

Example:

```text
case_id = case_001
  ├── doc_001.pdf
  ├── doc_002.pdf
  ├── doc_003.pdf
  └── ...
```

Each document has state:

```text
uploaded
queued_for_parse
parsing
parsed
indexing
indexed
extraction_ready
extracted
failed
needs_review
```

Each page can also have state:

```text
page_uploaded
rendered
ocr_done
layout_done
parsed
indexed
failed
```

This lets your system continue even if one document fails.

---

## 3. Fan-out / fan-in architecture

For hundreds of documents, use a **fan-out/fan-in** pattern.

### Fan-out

Split the work:

```text
case
→ documents
→ pages
→ parser tasks
```

Example:

```text
300 documents
→ 300 document parsing jobs
→ maybe 3,000 page-level parsing jobs
```

### Fan-in

After enough outputs are ready:

```text
parsed pages/tables/blocks
→ indexing
→ extraction
→ aggregation
→ validation
```

This is a common workflow orchestration pattern. AWS Step Functions Distributed Map is explicitly designed for large-scale parallel workloads; it runs each item as a child workflow execution and supports high concurrency over large S3 datasets. ([AWS Documentation][2])

Celery has workflow primitives like `chain`, `group`, and `chord`; a `chord` is especially relevant when you run many parsing tasks in parallel and then run a callback after all tasks finish. ([Celery Documentation][3])

---

## 4. Do not wait for all documents if you do not need to

This is the biggest design improvement.

For some schemas, extraction can begin before every document is parsed.

Example schema:

```python
class FinancialBundleSchema(BaseModel):
    company_name: str
    reporting_period: str
    total_revenue: float
    net_income: float
    operating_cash_flow: float
    risk_factors: list[str]
```

If the system already parsed:

```text
annual_report.pdf
audited_financials.pdf
```

It can start extracting:

```text
company_name
reporting_period
total_revenue
net_income
operating_cash_flow
```

Even if these documents are still parsing:

```text
appendix.pdf
old_investor_deck.pdf
supporting_notes.pdf
```

So the better design is:

```text
extract field when enough relevant evidence is ready
```

Not:

```text
extract only after all documents are parsed
```

This is called **incremental extraction**.

---

## 5. Use document classification early

Before full parsing, do a cheap first pass:

```text
filename
first page thumbnail
first page OCR
metadata
quick page count
```

Then classify documents:

```text
annual report
financial statement
invoice
receipt
contract
bank statement
appendix
irrelevant
unknown
```

This lets you prioritize parsing.

Example:

```text
High priority:
- annual_report.pdf
- audited_financial_statement.pdf
- cash_flow_statement.pdf

Low priority:
- old appendix
- marketing brochure
- irrelevant scanned attachment
```

Then your queue can process high-value documents first.

---

## 6. Use priority queues

You should not parse 300 documents in arbitrary order.

Use queues like:

```text
parse_high_priority
parse_normal
parse_low_priority
vlm_expensive
indexing
extraction
review
```

Example priority logic:

```text
If document likely contains schema-critical fields:
  parse first

If document is very large but likely irrelevant:
  parse later or only parse first few pages

If document is OCR-heavy:
  send to OCR/GPU queue

If document is clean born-digital:
  send to cheaper CPU parser queue
```

This matters because OCR and layout/VLM processing are usually the bottlenecks. A recent production Document AI microservice paper reports that OCR, not LLM parsing, dominated end-to-end latency, and that throughput saturated based on shared GPU inference capacity rather than simply adding more workers. ([arXiv][4])

---

## 7. Use two-level parsing: quick parse first, deep parse later

For hundreds of documents, do not deep-parse everything immediately.

Use:

```text
Quick Parse
→ classification/routing
→ decide which documents/pages need Deep Parse
```

### Quick parse

Cheap:

```text
metadata
page count
first page image
first few OCR lines
filename
simple text extraction
basic page thumbnails
```

### Deep parse

Expensive:

```text
OCR
layout detection
table extraction
chart extraction
PaddleOCR-VL / VLM
high-resolution page rendering
```

Pipeline:

```text
upload
→ quick parse all docs
→ classify/rank
→ deep parse only likely relevant docs/pages first
→ defer low-value docs
```

This saves a lot of time and money.

---

## 8. Use page-level parallelism, but control concurrency

If one document has 500 pages, one worker parsing it page-by-page becomes slow.

Better:

```text
document
→ render pages
→ parse pages in parallel
→ merge page outputs
```

But do not create infinite tasks. Use bounded concurrency.

Example:

```text
max 10 documents parsing concurrently
max 50 pages OCR concurrently
max 5 VLM calls concurrently
```

Why? Because your bottleneck may be:

```text
GPU memory
OCR server throughput
LLM rate limits
disk IO
database writes
```

Temporal’s best-practice docs warn that when using child workflows, fan-out size should be controlled and batching is preferred instead of one child workflow per tiny work item at huge scale. ([Temporal Docs][5])

So use batching:

```text
one task = parse pages 1–10
one task = parse pages 11–20
```

Not always:

```text
one task = one page
```

---

## 9. Extraction should be event-driven

Do not run extraction by polling everything manually.

Use events.

Example:

```text
DocumentParsed event
PageIndexed event
HighPriorityEvidenceReady event
AllRequiredEvidenceReady event
ExtractionCompleted event
```

Flow:

```text
parser worker finishes page/table/block
→ emits PageParsed
→ indexer indexes page evidence
→ emits PageIndexed
→ extraction planner checks which fields can run
→ starts extraction for ready fields
```

So extraction can start incrementally.

---

## 10. Use “field readiness” instead of “case readiness”

This is important.

Traditional:

```text
case is ready only when all documents parsed
```

Better:

```text
field is ready when enough relevant evidence exists
```

Example:

```text
company_name:
  ready after first cover page parsed

total_revenue:
  ready after income statement page parsed

risk_factors:
  ready after risk section parsed

revenue_by_segment:
  ready after segment table/chart pages parsed
```

Field status:

```text
waiting_for_evidence
ready_to_extract
extracting
validated
conflict
needs_review
missing
```

This enables partial results.

The user can see:

```text
company_name: extracted
total_revenue: extracted
risk_factors: still waiting for relevant pages
```

---

## 11. Store parse artifacts as immutable outputs

Every parsing result should be saved.

For each document/page, store:

```text
raw file
page image
OCR text
layout boxes
tables
chart crops
Markdown
parser version
parse quality score
timestamp
```

Do not overwrite casually. Version them:

```text
doc_001/page_004/parser=docling/v1/result.json
doc_001/page_004/parser=paddleocr_vl/v1/result.json
doc_001/page_004/parser=doclayout_yolo/v1/result.json
```

Why?

```text
1. Reproducibility
2. Debugging
3. Re-running extraction without re-parsing
4. Comparing parsers
5. Human review
6. Audit trail
```

This lets you run extraction many times with different schemas without parsing again.

---

## 12. Separate parsing from extraction completely

In production, parsing and extraction should be independent services.

```text
Parsing Service:
raw documents → parsed evidence

Indexing Service:
parsed evidence → searchable index

Extraction Service:
schema + evidence index → structured JSON

Review Service:
field results → human correction/finalization
```

This lets you scale them separately.

Example:

```text
Parsing workers: GPU/OCR heavy
Indexing workers: CPU/IO heavy
Extraction workers: LLM/API heavy
Review service: user-facing
```

The microservice architecture paper I found makes the same point: separate GPU-bound inference from CPU-bound orchestration, use asynchronous processing for IO-heavy operations, and horizontally scale components independently. ([arXiv][4])

---

## 13. Recommended architecture

```text
                    ┌────────────────────┐
                    │ Upload API          │
                    └─────────┬──────────┘
                              │
                    ┌─────────▼──────────┐
                    │ Case + Job DB       │
                    │ statuses/progress   │
                    └─────────┬──────────┘
                              │
                    ┌─────────▼──────────┐
                    │ Object Storage      │
                    │ raw files/pages     │
                    └─────────┬──────────┘
                              │
              ┌───────────────▼────────────────┐
              │ Workflow Orchestrator           │
              │ Temporal / Celery / Step Func   │
              └───────┬───────────────┬────────┘
                      │               │
        ┌─────────────▼─────┐   ┌─────▼────────────┐
        │ Parsing Workers    │   │ OCR/VLM Workers   │
        │ CPU parsers        │   │ GPU/API limited   │
        └─────────────┬─────┘   └─────┬────────────┘
                      │               │
              ┌───────▼───────────────▼───────┐
              │ Parse Artifact Store           │
              │ markdown/json/tables/bboxes     │
              └───────────────┬───────────────┘
                              │
                    ┌─────────▼──────────┐
                    │ Indexing Service    │
                    │ keyword/vector      │
                    └─────────┬──────────┘
                              │
                    ┌─────────▼──────────┐
                    │ Extraction Planner  │
                    │ field readiness     │
                    └─────────┬──────────┘
                              │
                    ┌─────────▼──────────┐
                    │ Extraction Workers  │
                    │ field-level LLM     │
                    └─────────┬──────────┘
                              │
                    ┌─────────▼──────────┐
                    │ Validation/Review   │
                    └────────────────────┘
```

---

## 14. Good orchestration choices

### Option A: Celery + Redis/RabbitMQ

Good for startup/FastAPI prototype.

Use:

```text
Celery group/chord/chain
```

Example:

```text
group(parse_document.s(doc_id) for doc_id in docs)
→ chord callback: index_case
→ extraction jobs
```

Celery Canvas supports chains for sequential task linking and chords for running a group of tasks followed by a callback. ([Celery Documentation][3])

Use Celery if:

```text
you want Python-native
you want quick implementation
your workflow complexity is moderate
```

### Option B: Temporal

Better for serious long-running workflows.

Use Temporal if:

```text
jobs may run overnight/days
you need retries
you need durable workflow state
you need human-in-the-loop waits
you need signals/cancellation
you need visibility into workflow history
```

Temporal is stronger than Celery for long-running orchestration.

### Option C: AWS Step Functions Distributed Map

Use if you are on AWS and want managed orchestration.

Best for:

```text
large S3 document batches
high parallelism
serverless orchestration
large fan-out/fan-in
```

AWS Distributed Map supports large-scale parallel workloads and child workflow executions. ([AWS Documentation][2])

### Option D: Google Workflows + Document AI LRO

Use if you are using Google Document AI.

Google Document AI batch processing returns a long-running operation because processing can take longer than a normal API response, and results can be written to Cloud Storage. ([Google Cloud Documentation][1])

---

## 15. What I recommend for your current stage

Since you are early and likely using FastAPI/Python:

```text
MVP:
FastAPI + PostgreSQL + Redis + Celery + S3/MinIO/local storage
```

Later, when workflows become complex:

```text
Production:
Temporal + PostgreSQL + object storage + separate worker pools
```

My recommendation:

```text
Start with Celery if you need to build fast.
Move to Temporal if:
- jobs run overnight often
- human review pauses workflows
- retry/cancellation/progress become complex
```

---

## 16. Database state model

You need persistent state tables.

### `cases`

```text
case_id
user_id
status
total_documents
parsed_documents
indexed_documents
extracted_fields
failed_documents
created_at
updated_at
```

### `documents`

```text
document_id
case_id
filename
storage_path
page_count
status
priority
document_type
parse_quality
error_message
created_at
updated_at
```

### `pages`

```text
page_id
document_id
page_number
status
image_path
ocr_status
layout_status
parse_status
index_status
error_message
```

### `parse_artifacts`

```text
artifact_id
document_id
page_number
parser_name
parser_version
artifact_type
storage_path
created_at
```

### `extraction_jobs`

```text
job_id
case_id
schema_id
status
fields_total
fields_completed
fields_needing_review
created_at
updated_at
```

### `field_results`

```text
field_result_id
job_id
field_path
status
value_json
confidence
selected_evidence_ids
validation_errors
```

---

## 17. Status design

Use clear statuses.

### Case statuses

```text
uploaded
quick_parsing
deep_parsing
partially_indexed
extracting
partially_extracted
needs_review
completed
failed
cancelled
```

### Document statuses

```text
uploaded
queued
quick_parsing
deep_parsing
parsed
indexed
failed
skipped
```

### Field statuses

```text
waiting_for_evidence
ready
extracting
validated
missing
conflict
low_confidence
needs_review
human_corrected
```

This makes the UI understandable.

---

## 18. Extraction dependency model

Each field should declare what evidence it depends on.

Example:

```json
{
  "field_path": "total_revenue",
  "required_evidence_types": ["table", "text_block"],
  "preferred_document_types": ["annual_report", "financial_statement"],
  "keywords": ["revenue", "net sales", "income statement"],
  "min_evidence_count": 1
}
```

Then the extraction planner can check:

```text
Do we have enough indexed evidence for total_revenue?
Yes → extract
No → wait or mark missing after all relevant documents parsed
```

This is better than waiting for all parsing to finish.

---

## 19. Incremental extraction example

Imagine 300 documents.

### Hour 0

```text
User uploads 300 PDFs.
System creates case_id.
All docs are queued.
```

### Hour 1

Quick parse finishes:

```text
annual_report.pdf: high priority
audited_financials.pdf: high priority
appendix_1.pdf: low priority
marketing_brochure.pdf: low priority
```

### Hour 2

High-priority docs are parsed and indexed.

Extraction starts for fields:

```text
company_name: done
reporting_period: done
total_revenue: done
net_income: done
```

### Hour 4

More docs parse.

Extraction continues:

```text
risk_factors: done
segment_revenue: conflict detected
operating_cash_flow: done
```

### Overnight

Low-priority docs finish.

System checks if any missing fields can now be filled.

### Morning

User sees:

```text
92% fields completed
5 fields need review
3 fields missing
all evidence available
```

This is the correct production UX.

---

## 20. Handling overnight parsing

For overnight jobs, support:

```text
resume
retry
cancel
pause
priority change
partial result preview
notifications
```

### Do not make user wait until the end

Show:

```text
Documents parsed: 120 / 300
Pages parsed: 1,840 / 4,500
Fields extracted: 42 / 60
Fields needing review: 5
Estimated remaining documents: 180
```

Do not overpromise exact time remaining; show progress percentages and active stages.

---

## 21. Failure handling

Some documents will fail.

Do not fail the whole case because one file fails.

Use:

```text
document-level failure isolation
page-level retry
parser fallback
manual upload correction
human review
```

Example:

```text
doc_045 failed with Docling
→ retry with PyMuPDF
→ retry with OCR
→ if still failed, mark document failed
→ continue with other docs
```

For a page:

```text
page 17 OCR failed
→ retry lower resolution
→ retry different OCR
→ mark page low_quality
```

Extraction can still continue using other evidence.

---

## 22. Parser caching

Parsing is expensive. Cache aggressively.

Use a file hash:

```text
sha256(file bytes)
```

If same file uploaded again:

```text
reuse parse artifacts
reuse page images
reuse embeddings
```

This saves huge time.

Also cache:

```text
page images
OCR outputs
layout outputs
table extraction outputs
embeddings
page summaries
```

---

## 23. Re-running extraction without re-parsing

This is crucial.

User may change schema after overnight parsing.

Do not reparse.

Instead:

```text
existing parse artifacts + new schema
→ run new extraction job
```

That is why parse artifacts must be stored separately from extraction results.

---

## 24. Backpressure and rate limits

You need backpressure.

Examples:

```text
Only 5 VLM jobs at once.
Only 20 OCR jobs at once.
Only 100 CPU parse jobs at once.
Only 10 LLM extraction jobs at once.
```

Use worker pools:

```text
cpu_parse_workers
gpu_ocr_workers
vlm_api_workers
llm_extraction_workers
indexing_workers
```

Each has concurrency limits.

Without this, one large batch can kill your server.

---

## 25. Practical FastAPI + Celery architecture

```text
FastAPI:
  handles upload, job creation, status APIs

PostgreSQL:
  stores cases, docs, jobs, fields, statuses

Object storage:
  stores raw PDFs, page images, parse JSON, markdown

Redis/RabbitMQ:
  broker for Celery

Celery workers:
  parse_worker
  ocr_worker
  index_worker
  extraction_worker
  review_worker
```

### Task flow

```python
@app.post("/cases/{case_id}/documents")
def upload_documents(...):
    # Save files
    # Create document rows
    # Queue quick_parse_case.delay(case_id)
    return {"case_id": case_id, "status": "queued"}
```

Celery flow:

```python
quick_parse_case(case_id)
    → classify_documents
    → enqueue_deep_parse for high-priority docs
    → enqueue_indexing when parsed
    → enqueue_extraction_when_ready
```

For simple prototype:

```text
group(parse_document.s(doc_id) for doc_id in document_ids)
```

For callback after all finish:

```text
chord(group(parse_document.s(doc_id) for doc_id in document_ids))(index_case.s(case_id))
```

But for hundreds/overnight jobs, I would prefer event-driven incremental extraction over one giant chord.

---

## 26. Why not one giant chord only?

A single giant chord means:

```text
wait for all parse tasks
→ then extract
```

That is simple but not optimal.

Problem:

```text
one slow/failed document blocks extraction
low-priority irrelevant docs delay important fields
no partial results
bad user experience
```

Better:

```text
parse high-priority docs first
index as each doc/page finishes
extract fields as soon as evidence is ready
continue parsing remaining docs in background
```

Use chords for subgroups:

```text
parse pages 1–20 of doc_001
→ merge document parse

parse high-priority document group
→ trigger early extraction

parse all documents
→ final completeness check
```

---

## 27. Human review can start before all processing finishes

For long jobs, reviewers can begin earlier.

Example:

```text
Field total_revenue has conflict after first 20 docs.
Reviewer can resolve it while remaining docs parse.
```

But be careful: later documents may add new evidence.

So use review states:

```text
provisional_review
final_review
locked
```

If new evidence appears after review:

```text
field was reviewed
new conflicting evidence found
→ reopen field or flag changed_evidence
```

---

## 28. Important UX design

The user should see:

```text
Case progress
Document progress
Field progress
Errors
Review queue
Partial outputs
Final export availability
```

Example:

```text
Case: Financial Bundle 2025

Parsing:
  185 / 300 documents parsed
  2,430 / 4,800 pages parsed

Indexing:
  160 documents indexed

Extraction:
  47 / 60 fields completed
  8 fields waiting for evidence
  5 fields need review

Failures:
  3 documents failed OCR
```

This is a real product workflow.

---

## 29. Recommended production algorithm

```text
1. User uploads batch.
2. Create case and document records.
3. Store raw files.
4. Quick parse all documents.
5. Classify and prioritize documents.
6. Fan out deep parsing by document/page batches.
7. Save parse artifacts durably.
8. Index each parsed artifact immediately.
9. Check field readiness after indexing.
10. Run extraction for ready fields.
11. Validate extracted fields.
12. If conflict/low confidence, send field to review queue.
13. Continue parsing remaining documents.
14. Final completeness pass after all docs parsed or skipped.
15. Generate final JSON/evidence report.
```

---

## 30. Final answer

Production systems handle hundreds of documents by treating the pipeline as a **durable asynchronous workflow**, not a synchronous script.

The key ideas are:

```text
1. Use long-running jobs, not request-response.
2. Fan out parsing by document/page/batch.
3. Store parse outputs durably.
4. Index parsed evidence as soon as it is ready.
5. Start extraction incrementally, field-by-field.
6. Use field readiness, not case readiness.
7. Prioritize important documents/pages first.
8. Separate CPU parsing, GPU/OCR, indexing, and LLM extraction workers.
9. Cache parse artifacts so extraction can be rerun without reparsing.
10. Track progress, retries, failures, and partial results.
```

For your current build, I would start with:

```text
FastAPI + PostgreSQL + Redis + Celery + object storage
```

Then move to:

```text
Temporal
```

when long-running workflows, retries, cancellation, and human review become complex.

The most important design shift is:

```text
Parsing and extraction are not one blocking pipeline.
They are separate durable stages connected by saved artifacts and events.
```

[1]: https://docs.cloud.google.com/document-ai/docs/long-running-operations?utm_source=chatgpt.com "Managing long-running operations (LROs) | Document AI"
[2]: https://docs.aws.amazon.com/step-functions/latest/dg/state-map-distributed.html?utm_source=chatgpt.com "Using Map state in Distributed mode for large-scale ..."
[3]: https://docs.celeryq.dev/en/stable/userguide/canvas.html?utm_source=chatgpt.com "Canvas: Designing Work-flows — Celery 5.6.3 documentation"
[4]: https://arxiv.org/abs/2605.18818?utm_source=chatgpt.com "Operationalizing Document AI: A Microservice Architecture for OCR and LLM Pipelines in Production"
[5]: https://docs.temporal.io/best-practices/managing-aps-limits?utm_source=chatgpt.com "Managing Actions per Second (APS) limits in Temporal ..."
