# BExtract vs Production Codebase — Searching & Extraction Mechanism Architecture

> **Purpose:** A comprehensive technical comparison of how the reference codebase (`BExtract/`) and the production codebase (`backend/`, `src/`) handle document chunking, evidence retrieval, field extraction, multi-document processing, and cross-page extraction.

---

## Table of Contents

1. [Architecture at a Glance](#1-architecture-at-a-glance)
2. [Search &amp; Retrieval Mechanism](#2-search--retrieval-mechanism)
3. [Extraction Tiers &amp; Techniques](#3-extraction-tiers--techniques)
4. [Chunking Strategies](#4-chunking-strategies)
5. [Models &amp; Embeddings](#5-models--embeddings)
6. [Multi-Document Extraction](#6-multi-document-extraction)
7. [Cross-Page Extraction](#7-cross-page-extraction)
8. [Critic &amp; Validation Mechanism](#8-critic--validation-mechanism)
9. [Evidence Visualization](#9-evidence-visualization)
10. [Database &amp; Storage](#10-database--storage)
11. [Side-by-Side Feature Matrix](#11-side-by-side-feature-matrix)
12. [Key Architectural Differences &amp; Trade-offs](#12-key-architectural-differences--trade-offs)

---

## 1. Architecture at a Glance

| Aspect                        | BExtract (Reference)                                             | Production (`backend/` + `src/`)                                                                                          |
| ----------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| **Backend framework**   | FastAPI + Google ADK (Agent Development Kit)                     | FastAPI (no ADK)                                                                                                              |
| **Frontend framework**  | Next.js (static export)                                          | React + Vite                                                                                                                  |
| **Extraction paradigm** | **Agentic workflow graph** (ADK multi-agent)               | **Chunk-first RAG pipeline** (retriever → LLM)                                                                         |
| **LLM provider**        | Google Gemini (primary)                                          | OpenAI GPT-5-mini (primary), Gemini (fallback)                                                                                |
| **Search type**         | Hybrid: pgvector dense + BM25 sparse + RRF fusion                | Hybrid: pgvector cosine + PostgreSQL FTS (ts_rank)                                                                            |
| **Embedding model**     | `gemini-embedding-001` (3072-dim)                              | `text-embedding-3-small` (1536-dim) primary; local `all-mpnet-base-v2` (768-dim) fallback; Gemini 3072-dim (agentic tier) |
| **ORM**                 | Prisma                                                           | SQLAlchemy                                                                                                                    |
| **Vector DB**           | PostgreSQL + pgvector                                            | PostgreSQL + pgvector                                                                                                         |
| **Retry mechanism**     | Critic agent routes back to failed field + 3x empty-result retry | 3-attempt progressive retrieval widening + critic (agentic only)                                                              |

### High-Level Pipeline Comparison

```
BExtract Pipeline:
  PDF → PyMuPDF parse → chunk (260 tokens, 40 overlap) → embed (Gemini 3072-d)
     → pgvector + BM25 → ADK agent (Gemini 3.5 Flash) → critic agent → retry → DB

Production Pipeline:
  PDF → Parser (Mistral OCR / PaddleOCR / pdfplumber / Docling) → evidence cleaner
     → chunk (5 strategies) → embed (OpenAI 1536-d) → FTS + pgvector hybrid
     → OpenAI gpt-5-mini → regex fallback → Gemini fallback → critic (agentic) → DB
```

---

## 2. Search & Retrieval Mechanism

### BExtract — Hybrid: pgvector Dense + BM25 Sparse + Reciprocal Rank Fusion

**File:** `BExtract/server/custom_tools.py`

BExtract uses a **three-stage hybrid search** with explicit fusion:

```
                    ┌─ Dense (pgvector cosine) → top 10 ─┐
Query embed ───────►│                                    ├──► RRF Fusion (k=60) ──► top 3 chunks
                    └─ Sparse (BM25Okapi) → top 10 ──────┘
```

| Component               | How it works                                                                                                                            | Key code                    |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| **Dense search**  | Query embedded with`gemini-embedding-001` (3072-d, `task_type="retrieval_query"`), pgvector `<=>` cosine distance, top 10 results | `custom_tools.py:30-66`   |
| **Sparse (BM25)** | All chunks for the document tokenized with regex`[a-zA-Z0-9_]+`, ranked with `rank_bm25.BM25Okapi`, top 10                          | `custom_tools.py:69-104`  |
| **RRF Fusion**    | Reciprocal Rank Fusion with`rank_constant = 60`, returns top 3 fused chunks by default                                                | `custom_tools.py:107-115` |

**Scope:** All searches are scoped to a single `document_id` via a `ContextVar` (`custom_tools.py:15`). Each file in a batch gets its own search context.

**Agent-driven:** The search is exposed as a `FunctionTool` that the ADK LLM agents call autonomously — the agent decides when and how to search.

### Production — Hybrid: pgvector Cosine + PostgreSQL FTS (ts_rank)

**File:** `backend/app/db/repositories/evidence_repo.py:13-51`

Production uses a **weighted SQL hybrid** with a three-tier fallback cascade:

```
Query embed ──► HYBRID: 0.4 × ts_rank(FTS) + 0.6 × cosine_sim(pgvector) → top K
                    │ (if no embedding or empty results)
                    ▼
              FTS ONLY: ts_rank(plainto_tsquery) → top K
                    │ (if still empty)
                    ▼
              KEYWORD: to_tsvector @@ plainto_tsquery OR text ILIKE '%query%' → top K
```

| Component                           | How it works                                                                                        | Key code                   |
| ----------------------------------- | --------------------------------------------------------------------------------------------------- | -------------------------- |
| **FTS (PostgreSQL built-in)** | `ts_rank(tsv_search, plainto_tsquery('english', :query))` with GIN index on `tsv_search` column | `evidence_repo.py:13-51` |
| **Weighted fusion**           | `hybrid_score = 0.4 × fts_score + 0.6 × vector_score` — a fixed linear combination, NOT RRF    | `evidence_repo.py:48-50` |
| **Keyword fallback**          | `text ILIKE '%query%'` ordered by `created_at DESC`                                             | `evidence_repo.py:75-97` |

**Progressive widening:** Retrieval is not a single pass — it uses a **3-attempt progressive strategy** (`progressive_retrieval.py:14-41`):

| Attempt | top_k                           | Source filter                                         | Purpose            |
| ------- | ------------------------------- | ----------------------------------------------------- | ------------------ |
| 1       | **3**                     | Preferred source type (e.g.`table_row` for numbers) | Fast, targeted hit |
| 2       | **8**                     | None                                                  | Broader recall     |
| 3       | `max_evidence_items` (budget) | None                                                  | Maximum recall     |

**Scope:** Searches are scoped to a `case_id`. In single-doc mode, one case = one document. In cross-document mode, one case = all bundled documents.

### Key Difference: BM25 vs ts_rank

|                            | BExtract                                                                    | Production                                                          |
| -------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| **Sparse retrieval** | **BM25Okapi** (in-memory Python library, IDF-weighted term frequency) | **PostgreSQL `ts_rank`** (built-in FTS, no IDF weighting)   |
| **Fusion method**    | **Reciprocal Rank Fusion (RRF)** — rank-based, parameter-light       | **Weighted linear combination** — `0.4×FTS + 0.6×vector` |
| **In-memory vs SQL** | BM25 runs in Python (loads all chunks into memory)                          | Entire search runs in PostgreSQL SQL                                |
| **Adaptiveness**     | RRF adapts to rank distributions                                            | Fixed weights don't adapt                                           |

---

## 3. Extraction Tiers & Techniques

### BExtract — Two Approaches, No Tiers

**Files:** `BExtract/server/pipeline.py`, `BExtract/server/main.py`

BExtract offers **two extraction approaches** (not tiers), selectable at runtime:

| Approach                               | How it works                                                                                                                                                                                                                                                                                                                 |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Agentic (ADK Workflow)**       | Builds a dynamic Google ADK`Workflow` graph from template items. Each field gets a `FunctionNode` (prepare) + `LlmAgent` (extract) + collect node. The graph runs: `prepare → extract → collect → compile_payload → critic_agent → route_critic_result → db_commit`. Uses `gemini-3.5-flash` for all agents. |
| **Pre-Injected RAG (stateless)** | For each field: generates a search query → calls`document_hybrid_search()` → injects top chunks into a Gemini prompt → extracts value. Retries empty fields up to 3 times with wider search.                                                                                                                            |

Both approaches use the **same models** and **same search** — the difference is orchestration (ADK multi-agent graph vs sequential per-field calls).

### Production — Three Tiers (Effectively Binary)

**Files:** `backend/app/extraction/agentic_controller.py`, `backend/app/services/production_extraction.py`

Production defines three tiers, but they collapse to **effectively binary**:

| Tier                         | `agentic` flag | What changes                                                                  |
| ---------------------------- | ---------------- | ----------------------------------------------------------------------------- |
| **`cost_effective`** | `False`        | No Gemini 3072-d index embeddings; critic disabled; no`model_used` tracking |
| **`agentic`**        | `True`         | Gemini 3072-d index embeddings; critic enabled; per-attempt model tracking    |
| **`agentic_plus`**   | `True`         | **Identical to `agentic`** — dead code, never branched differently   |

> **Key finding:** The `_extractor_for_mode()` function (`production_extraction.py:321-322`) **always returns `AgenticFieldExtractor`** regardless of the `agentic` flag. The `FieldExtractor` (pure deterministic) is only used as an inner fallback inside `AgenticFieldExtractor`. So **all tiers call OpenAI gpt-5-mini** for extraction.

#### The Extraction Chain (all tiers, per field, per attempt)

```
                     ┌─ 1. OpenAI gpt-5-mini (Responses API, JSON mode)
                     │     confidence: 0.85 default
                     │     timeout: 60s
                     │     ↓ returns None on no key / no evidence / error
AgenticFieldExtractor├─ 2. Deterministic regex rules (FieldExtractor)
                     │     label matching: "<label> [:=|-] value"
                     │     confidence: 0.72-0.86
                     │     ↓ returns empty if no match
                     └─ 3. Gemini 2.5-flash fallback (google-genai SDK)
                           confidence: 0.68 default
```

**File:** `backend/app/extraction/agentic_controller.py:75-87`

#### The Per-Field Retry Loop

**File:** `backend/app/services/production_extraction.py:160-235`

```
for attempt in [1, 2, 3]:
    pack = retriever.retrieve(case_id, plan, attempt)     # progressive widening
    candidates = extractor.extract(field_path, schema, pack)
    if not candidates:
        continue                                           # retry with wider evidence
    if detect_conflict(values):
        record conflict
    final_value = resolve_candidates(candidates)           # highest confidence wins
    if status == "validated":
        break                                              # early exit
```

**Only validated fields exit early.** Fields with `low_confidence`, `conflict`, or `invalid` keep retrying against progressively wider evidence windows.

### Key Difference: ADK Multi-Agent vs Chunk-First RAG

|                               | BExtract                                                                     | Production                                                              |
| ----------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| **Orchestration**       | Google ADK`Workflow` graph — multi-agent, event-driven, stateful sessions | Linear per-field loop — stateless per field, progressive retrieval     |
| **Agent autonomy**      | Agents call search tool autonomously (LLM decides when to search)            | Search is pre-planned by`FieldRetrievalPlanner` (deterministic query) |
| **LLM calls per field** | 1+ (agent may search multiple times)                                         | 1 per attempt (up to 3 attempts), plus fallback chain                   |
| **State management**    | ADK`InMemorySessionService` with full event history                        | Stateless — each field processed independently                         |

---

## 4. Chunking Strategies

### BExtract — Single Strategy: Sliding Window

**File:** `BExtract/server/ingestion.py:104-136`

BExtract uses **one chunking strategy**: overlapping token windows.

| Parameter        | Value                                                              |
| ---------------- | ------------------------------------------------------------------ |
| Window size      | **260 tokens**                                               |
| Overlap          | **40 tokens**                                                |
| Tokenization     | Word-level coordinates via PyMuPDF (fitz)                          |
| Page boundaries  | Windows**do NOT span pages** — reset per page               |
| Word coordinates | Every word carries`(x0, y0, x1, y1)` bbox for highlight overlays |
| Chunk metadata   | `chunk_id`, `page`, `bbox`, `word_coordinates`             |

Tables are **not** specially chunked — they're part of the page text that gets windowed.

### Production — Five Strategies

**File:** `backend/app/services/chunker.py`

Production offers **five selectable strategies** dispatched via `chunk_parser_result()`:

| Strategy                     | Description                                              | Granularity                            | Overlap                                | Key use case                                 |
| ---------------------------- | -------------------------------------------------------- | -------------------------------------- | -------------------------------------- | -------------------------------------------- |
| **`page`** (DEFAULT) | One chunk per page                                       | Per-page                               | None                                   | General documents, balanced precision/recall |
| **`block`**          | One chunk per cleaned evidence block                     | Per-block (paragraphs, tables, images) | None                                   | Structured docs with clear block boundaries  |
| **`table_row`**      | Tables decomposed into one self-describing chunk per row | Per-row                                | None                                   | Tabular data extraction (financial tables)   |
| **`sliding_window`** | Token-aware overlapping windows                          | Variable                               | **80 tokens** (500-token window) | Dense text, narrative documents              |
| **`document`**       | Single chunk for entire document                         | Whole-doc                              | None                                   | Simple docs, schema generation preview       |

#### Table Row Chunking (unique to production)

When strategy = `table_row`, each table row becomes a self-contained markdown chunk with the header repeated:

```markdown
| Column A | Column B | Column C |
| --- | --- | --- |
| Value 1 | Value 2 | Value 3 |
```

This means the LLM sees the header context for every row — critical for accurate tabular extraction.

**File:** `backend/app/services/chunker.py:202-278`, row rendering at `:394-398`

#### Sliding Window (only strategy with overlap)

```
size = 500 tokens, overlap = 80 tokens, step = 420 tokens
Windows are PER PAGE — do NOT span page boundaries
Tokenization: tiktoken cl100k_base (fallback: whitespace)
```

**File:** `backend/app/services/chunker.py:281-333`

### Key Difference

|                            | BExtract                        | Production                                                                          |
| -------------------------- | ------------------------------- | ----------------------------------------------------------------------------------- |
| **Strategies**       | 1 (sliding window only)         | 5 (page, block, table_row, sliding_window, document)                                |
| **Table handling**   | Part of page text               | **Dedicated row-level decomposition** with self-describing markdown           |
| **Configurability**  | Fixed (260 tokens / 40 overlap) | Configurable (`chunk_size`, `chunk_overlap`, `max_table_rows`, `max_pages`) |
| **Word coordinates** | Every word has bbox             | Block/table-level bbox (not per-word)                                               |

---

## 5. Models & Embeddings

### Model Comparison

| Role                              | BExtract                                      | Production                                                                                                                              |
| --------------------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **Schema generation**       | Not LLM-generated (manual template)           | **gpt-5-mini** (from parsed markdown, not raw PDF)                                                                                |
| **Scalar extraction**       | `gemini-3.5-flash`                          | **gpt-5-mini** (primary) → regex → `gemini-2.5-flash` (fallback)                                                              |
| **Tabular extraction**      | `gemini-3.5-flash`                          | **gpt-5-mini** (same chain as scalar)                                                                                             |
| **Critic/validation**       | `gemini-3.5-flash` (dedicated critic agent) | Rule-based critic (missing fields + accounting identity check)                                                                          |
| **Evidence reconstruction** | N/A                                           | **gpt-5-mini** (repairs malformed OCR chunks)                                                                                     |
| **OCR (scanned PDFs)**      | Gemini vision or OpenAI GPT-4o vision         | Mistral OCR (primary), PaddleOCR-VL (fallback)                                                                                          |
| **Embeddings (corpus)**     | `gemini-embedding-001` (3072-d)             | `text-embedding-3-small` (1536-d) primary; `all-mpnet-base-v2` (768-d) fallback; `gemini-embedding-001` (3072-d) for agentic tier |

### Embedding Dimensions

```
BExtract:     3072-d (Gemini only)          ──► pgvector Vector(3072) + HNSW cosine

Production:   1536-d (OpenAI, primary)       ──► pgvector Vector(1536) + HNSW cosine
              3072-d (Gemini, agentic tier)  ──► pgvector Vector(3072) + HNSW cosine
               768-d (local, offline dev)    ──► same Vector(1536) column ⚠️
```

> **Note:** Production stores both OpenAI (1536-d) and Gemini (3072-d) embeddings in **separate columns** (`embedding` and `embedding_api`), each with its own HNSW index. The retrieval query uses whichever column matches the query embedding provider.

---

## 6. Multi-Document Extraction

### Current State

**Production multi-document extraction IS implemented end-to-end** — models, service, endpoint, frontend all wired.

### Two Modes

**File:** `backend/app/services/extraction_lab.py:490-636`

| Mode                         | Behavior                                                                                                                      | Output                                 |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| **`per_document`**   | Runs the full single-doc pipeline**independently** for each document. No merging.                                       | **N results** (one per document) |
| **`cross_document`** | Parses all docs, indexes all chunks into**one shared case**, runs **one extraction pass** over the pooled corpus. | **1 bundled result**             |

### Cross-Document Mechanism (How It Works)

```
                    ┌─ doc A chunks (prefixed: "docA:chk-...")
                    │
All input docs ────►├─ doc B chunks (prefixed: "docB:chk-...")  ──► ONE shared Case
                    │                                              ──► ONE evidence index
                    └─ doc C chunks (prefixed: "docC:chk-...")  ──► ONE extraction pass
                                                                   ──► ONE result
```

**Step-by-step** (`extraction_lab.py:505-636`):

1. Parse each document with the configured parser
2. Chunk each document using the selected strategy
3. Create **one synthetic `Case`** titled `"Extraction Lab Bundle: N documents"`
4. Register each doc as a `Document` under the shared case
5. Prefix chunk IDs with `{document_id}:` for provenance tracking
6. Index **all** chunks into one shared evidence index (with embeddings)
7. Call `run_case_extraction_db()` once — retrieval spans all documents
8. Return a single bundled result

### Constraints

| Constraint                          | Detail                                                                                         |
| ----------------------------------- | ---------------------------------------------------------------------------------------------- |
| **DB required**               | Cross-document mode raises HTTP 422 without a configured database                              |
| **No per-source attribution** | Output is a flat JSON; no field-to-document breakdown                                          |
| **Schema is shared**          | The same schema fields extract one value each; no field-to-document mapping                    |
| **Minimal test coverage**     | Only the payload-mapping helper is unit-tested (`test_extraction_evidence_layer.py:186-193`) |

### BExtract Multi-Document

BExtract handles multi-document via **batch processing** — each file is extracted independently with its own `ExtractionRun` and `FileExtraction` record. There is **no cross-document mode** — documents never share an evidence index.

---

## 7. Cross-Page Extraction

### BExtract

There is **no explicit cross-page mechanism**. The sliding window chunks (260 tokens, 40 overlap) are **per-page** — they do not span page boundaries. However:

- The ADK agent can call `document_hybrid_search()` multiple times, potentially retrieving chunks from different pages
- The LLM prompt includes all retrieved chunks, allowing it to "stitch" information across pages

### Production

Similarly, there is **no dedicated cross-page code path**. However, multiple mechanisms provide cross-page capability:

| Mechanism                           | How it works                                                                                                                                     |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Case-scoped retrieval**     | Hybrid search runs across all chunks in the case regardless of page number — top-K results naturally span pages                                 |
| **`document` strategy**     | The entire document is one chunk — all pages are in context                                                                                     |
| **`page` strategy + top-K** | Multiple page-chunks can be retrieved for a single field (up to`max_evidence_items`)                                                           |
| **LLM stitching**             | The OpenAI/Gemini prompt concatenates up to N evidence items (each truncated to 4000 chars) — the LLM can synthesize values from multiple pages |
| **Cross-document mode**       | When`cross_document` mode is selected, chunks from entirely different documents are pooled together                                            |

### Key Limitation (Both)

Neither system has **explicit spatial cross-page logic** — e.g., recognizing that a table header is on page 3 and its data rows continue on page 4. Cross-page extraction relies entirely on:

1. Retrieval ranking bringing the right chunks together
2. LLM reasoning to connect evidence from separate chunks

---

## 8. Critic & Validation Mechanism

### BExtract — LLM Critic Agent

**File:** `BExtract/server/pipeline.py:260-277`

BExtract uses a **dedicated LLM agent** as the critic:

```python
critic_agent = LlmAgent(
    name="critic_agent",
    model="gemini-3.5-flash",
    instruction="Review the compiled extraction JSON against the template
    requirements and mathematical/accounting constraints...
    Return strict JSON: status ('pass'/'fail'), failed_item_id,
    failed_item_type, critique, corrected_payload."
)
```

| Feature                  | Detail                                                                                                |
| ------------------------ | ----------------------------------------------------------------------------------------------------- |
| **Model**          | `gemini-3.5-flash` (separate LLM call)                                                              |
| **What it checks** | Template requirements + mathematical/accounting constraints                                           |
| **Output**         | `pass` / `fail` with `failed_item_id` and `corrected_payload`                                 |
| **Retry routing**  | If`fail` and `failed_item_id` is known, routes back to that item's prepare node for re-extraction |
| **Correction**     | Can provide`corrected_payload` — direct JSON fix                                                   |

### Production — Rule-Based Critic

**File:** `backend/app/extraction/agentic_controller.py:95-109`

Production uses a **lightweight rule-based critic** (no LLM call):

```python
def critic_issues(final_json, required_fields):
    issues = []
    # Check 1: missing required fields
    missing = [f for f in required_fields if _missing(final_json.get(f))]
    if missing:
        issues.append(f"missing_required:{','.join(missing)}")
    # Check 2: accounting identity (Assets = Liabilities + Equity, 2% tolerance)
    if assets and liabilities and equity:
        if abs(assets - (liabilities + equity)) > tolerance:
            issues.append("accounting_mismatch:assets_vs_liabilities_plus_equity")
    return issues
```

| Feature                        | Detail                                                                               |
| ------------------------------ | ------------------------------------------------------------------------------------ |
| **Model**                | None (pure Python rules)                                                             |
| **What it checks**       | (1) Required fields present, (2) Accounting identity (Assets = Liabilities + Equity) |
| **Output**               | List of issue strings                                                                |
| **Retry routing**        | Does NOT retry — just escalates status to`needs_review`                           |
| **Correction**           | None — flags issues for human review                                                |
| **Only in agentic tier** | Critic is**disabled** in `cost_effective` tier                               |

### Key Difference

|                        | BExtract                                      | Production                   |
| ---------------------- | --------------------------------------------- | ---------------------------- |
| **Critic type**  | LLM agent (`gemini-3.5-flash`)              | Rule-based (Python)          |
| **Cost**         | Extra LLM call per extraction                 | Free (in-memory checks)      |
| **Intelligence** | Can reason about complex constraints          | Limited to hardcoded rules   |
| **Correction**   | Can provide corrected JSON                    | Cannot correct — only flags |
| **Retry**        | Routes back to failed field for re-extraction | Escalates to`needs_review` |

---

## 9. Evidence Visualization

### BExtract — PDF Canvas with Word-Level Highlights

**File:** `BExtract/client/src/app/results/inspector/run-inspector-client.tsx`

| Feature                    | Detail                                                                                                                                |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **PDF rendering**    | PDF.js renders the actual PDF page to canvas                                                                                          |
| **Highlight method** | `highlightRectsFor()` matches extracted value tokens against **word-level coordinates** (every word has `(x0, y0, x1, y1)`) |
| **Precision**        | **Word-level** — can highlight individual words within a sentence                                                              |
| **Source**           | Chunks carry`word_coordinates` metadata from PyMuPDF                                                                                |

### Production — PDF Page Image with BBox Overlays

**File:** `src/views/extraction-lab-view.tsx` (`EvidenceDocumentViewer` component)

| Feature                    | Detail                                                                                                    |
| -------------------------- | --------------------------------------------------------------------------------------------------------- |
| **PDF rendering**    | Server-rendered page images via`parserBenchmarksApi.pageImageUrl()`                                     |
| **Highlight method** | Purple bbox overlays from`ExtractionEvidence` chunks (`x0, top, x1, bottom` converted to percentages) |
| **Precision**        | **Block/table-level** — highlights the evidence chunk's bounding box                               |
| **Bidirectional**    | Click field → highlights bbox; Click bbox → selects field                                               |

### Key Difference

|                               | BExtract                   | Production                                       |
| ----------------------------- | -------------------------- | ------------------------------------------------ |
| **Highlight precision** | Word-level                 | Block/table-level                                |
| **PDF rendering**       | Client-side (PDF.js)       | Server-side (page images)                        |
| **Overlay format**      | Canvas rectangles over PDF | CSS divs with percentage positions over`<img>` |
| **Interactivity**       | View source button         | **Bidirectional** (field ↔ bbox linkage)  |

---

## 10. Database & Storage

### BExtract — Prisma + pgvector

**File:** `BExtract/prisma/schema.prisma`

| Table                    | Key columns                                                                 |
| ------------------------ | --------------------------------------------------------------------------- |
| `DocumentTemplate`     | `config` (Json)                                                           |
| `ExtractionResult`     | `data` (Json), `confidence`, `status`                                 |
| `DocumentChunk`        | `chunk_text`, `embedding` (Vector 3072), `metadata` (Json)            |
| `PipelineRun`          | `status`, `totalCostUsd`, token counts                                  |
| `FileExtraction`       | `extractedPayload` (Json), `logs`, `sourceFilePath`                   |
| `VerifiedFieldExample` | `inputContextChunk`, `correctedValue` (Json) — for fine-tuning exports |

### Production — SQLAlchemy + pgvector

**File:** `backend/app/db/models.py`

| Table                 | Key columns                                                                                        |
| --------------------- | -------------------------------------------------------------------------------------------------- |
| `Case`              | `case_id`, `title`, `metadata_json`                                                          |
| `Document`          | `document_id`, `case_id` (FK), `source_path`                                                 |
| `EvidenceItem`      | `evidence_id`, `case_id`, `text`, `tsv_search` (TSVECTOR + GIN), `source_type`, `bbox` |
| `EvidenceEmbedding` | `embedding` (Vector 1536, HNSW cosine), `embedding_api` (Vector 3072, HNSW cosine)             |
| `ExtractionJob`     | `job_id`, `case_id`, `schema_json`, `result_json`, `status`, `consistency_report`      |
| `ExtractionAttempt` | `attempt_number`, `model_used`, `candidates_json`                                            |

### Key Difference

|                               | BExtract                       | Production                                                           |
| ----------------------------- | ------------------------------ | -------------------------------------------------------------------- |
| **ORM**                 | Prisma                         | SQLAlchemy                                                           |
| **FTS**                 | None (uses BM25 in Python)     | PostgreSQL`tsvector` + GIN index                                   |
| **Embedding columns**   | 1 (`Vector(3072)`)           | 2 (`Vector(1536)` + `Vector(3072)`)                              |
| **Attempt tracking**    | `logs` (free text)           | Dedicated`ExtractionAttempt` table with `model_used` per attempt |
| **Fine-tuning exports** | `VerifiedFieldExample` table | Not implemented                                                      |

---

## 11. Side-by-Side Feature Matrix

| Feature                       | BExtract                             | Production                                              |
| ----------------------------- | ------------------------------------ | ------------------------------------------------------- |
| **Search algorithm**    | pgvector + BM25 + RRF                | pgvector + FTS (ts_rank) + weighted linear              |
| **Sparse retrieval**    | BM25Okapi (Python, IDF-weighted)     | PostgreSQL ts_rank (no IDF)                             |
| **Fusion method**       | Reciprocal Rank Fusion (k=60)        | Weighted linear (0.4 FTS + 0.6 vector)                  |
| **Chunking strategies** | 1 (sliding window)                   | 5 (page, block, table_row, sliding_window, document)    |
| **Table chunking**      | Part of page text                    | Row-level decomposition with self-describing markdown   |
| **Overlap**             | 40 tokens (fixed)                    | 80 tokens (configurable, sliding_window only)           |
| **Primary LLM**         | `gemini-3.5-flash`                 | `gpt-5-mini`                                          |
| **LLM fallback chain**  | Gemini only                          | gpt-5-mini → regex → gemini-2.5-flash                 |
| **Critic**              | LLM agent (gemini-3.5-flash)         | Rule-based (missing fields + accounting identity)       |
| **Critic retry**        | Routes back to failed field          | Escalates to`needs_review`                            |
| **Extraction tiers**    | 2 approaches (agentic, pre-injected) | 3 tiers (effectively binary: cost_effective vs agentic) |
| **Retry attempts**      | 3 (empty-result verification)        | 3 (progressive retrieval widening)                      |
| **Multi-document**      | Batch only (independent)             | Per-document + cross-document (shared index)            |
| **Cross-page**          | LLM stitching (implicit)             | LLM stitching (implicit)                                |
| **Schema generation**   | Manual template builder              | LLM-generated (gpt-5-mini from markdown)                |
| **OCR**                 | Gemini/OpenAI vision                 | Mistral OCR / PaddleOCR-VL / Docling                    |
| **Evidence precision**  | Word-level bbox                      | Block/table-level bbox                                  |
| **Bidirectional UI**    | No                                   | Yes (field ↔ bbox linkage)                             |
| **Fine-tuning exports** | Yes (VerifiedFieldExample)           | No                                                      |
| **Cost tracking**       | Per-run token/cost                   | Per-attempt model tracking                              |

---

## 12. Key Architectural Differences & Trade-offs

### 1. Search Quality: BM25+RRF vs FTS+Weighted Linear

**BExtract's BM25 + RRF** is theoretically superior for sparse retrieval:

- BM25 applies IDF weighting (rare terms score higher)
- RRF is rank-based — robust to score scale differences between dense and sparse

**Production's ts_rank + weighted linear** is simpler and runs entirely in SQL:

- No need to load chunks into Python memory
- `ts_rank` doesn't apply IDF (all terms weighted equally)
- Fixed weights (0.4/0.6) don't adapt to query characteristics

**Trade-off:** BExtract gets better sparse matching at the cost of loading chunks into Python. Production gets faster SQL-only execution at the cost of lower-quality sparse matching.

### 2. Extraction: LLM-Only vs LLM+Rules Fallback Chain

**BExtract** relies on Gemini exclusively — if the API is down, extraction fails.

**Production** has a **three-tier fallback**: gpt-5-mini → deterministic regex → Gemini flash. This means:

- If OpenAI is down, regex rules still extract label-value pairs
- If regex fails, Gemini provides a final fallback
- **Zero-API mode** is possible (cost_effective tier with local embeddings + regex extraction)

### 3. Chunking: Single Strategy vs Multi-Strategy

**BExtract** uses one fixed strategy (260-token sliding window). This is simple but suboptimal for tables — table rows get split across windows.

**Production** offers five strategies. The `table_row` strategy is particularly powerful for financial documents — each row becomes a self-contained chunk with headers, so the LLM always knows the column context. The `page` strategy is the sensible default for general documents.

### 4. Multi-Document: Batch vs Cross-Document

**BExtract** only processes documents independently (batch mode).

**Production** supports both independent and **cross-document** extraction, where all documents are pooled into one shared evidence index. This is unique — a field's value can be grounded in evidence from *any* of the bundled documents. However, the output lacks per-source attribution.

### 5. Critic: LLM Intelligence vs Rule-Based Speed

**BExtract's LLM critic** is more flexible — it can reason about arbitrary constraints and provide corrected payloads. But it adds latency and cost (another Gemini call).

**Production's rule-based critic** is free and instant, but limited to hardcoded checks (missing fields + accounting identity). It cannot reason about domain-specific constraints.

---

## Summary

| Dimension                        | Winner     | Why                                                             |
| -------------------------------- | ---------- | --------------------------------------------------------------- |
| **Search quality**         | BExtract   | BM25 + RRF > ts_rank + fixed weights                            |
| **Search performance**     | Production | All-SQL, no Python memory loading                               |
| **Extraction robustness**  | Production | Three-tier fallback chain (LLM → regex → Gemini)              |
| **Chunking flexibility**   | Production | 5 strategies vs 1, dedicated table-row decomposition            |
| **Multi-document**         | Production | Cross-document mode with shared evidence index                  |
| **Critic intelligence**    | BExtract   | LLM critic can reason and correct                               |
| **Critic cost**            | Production | Free rule-based, no extra LLM call                              |
| **Evidence visualization** | Production | Bidirectional field ↔ bbox linkage                             |
| **Schema generation**      | Production | LLM auto-generates from natural language                        |
| **Fine-tuning pipeline**   | BExtract   | VerifiedFieldExample exports for training                       |
| **OCR quality**            | Tie        | BExtract uses vision LLMs; Production uses specialized OCR APIs |

> **Bottom line:** Production is more operationally robust (multi-strategy chunking, multi-model fallback, cross-document mode, auto-schema generation). BExtract has a more sophisticated search algorithm (BM25+RRF) and a smarter critic (LLM-based). The ideal system would combine Production's chunking and extraction resilience with BExtract's hybrid search quality.

