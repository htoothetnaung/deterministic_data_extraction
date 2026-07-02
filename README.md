# ExtractIQ — Deterministic Document Extraction & Benchmarking

A production-ready, full-stack platform for **benchmarking and testing deterministic
AI-based document data extraction**. Upload documents, review & correct OCR output,
build reusable extraction templates, apply them across a corporate document database,
and benchmark extraction quality, consistency, and latency.

- **Frontend**: Next.js 16 (App Router) · TypeScript · Tailwind CSS 4 · shadcn/ui · Recharts · Framer Motion · TanStack Query · Zustand
- **Backend**: Python FastAPI · Pydantic v2 · modular service architecture

---

## 1. Folder structure

```
my-project/
├── src/                          # ── Next.js frontend ──
│   ├── app/
│   │   ├── layout.tsx            # root layout + providers (theme, react-query)
│   │   ├── page.tsx              # single route → AppShell + view switcher
│   │   └── globals.css           # theme tokens (emerald/teal + slate)
│   ├── components/
│   │   ├── ui/                   # shadcn/ui component set (preinstalled)
│   │   └── app/                  # app-specific building blocks
│   │       ├── app-shell.tsx     # sidebar + topbar + sticky footer
│   │       ├── sidebar.tsx       # nav + theme toggle
│   │       ├── page-header.tsx
│   │       ├── stat-card.tsx
│   │       ├── section.tsx       # SectionCard + EmptyState
│   │       └── badges.tsx        # Status / Confidence / Bench badges
│   ├── views/                    # the 6 workflow views
│   │   ├── dashboard-view.tsx
│   │   ├── documents-view.tsx
│   │   ├── ocr-review-view.tsx
│   │   ├── templates-view.tsx
│   │   ├── apply-template-view.tsx
│   │   └── benchmarking-view.tsx
│   └── lib/
│       ├── api.ts                # typed API client (gateway-aware)
│       ├── types.ts              # TS types mirroring Pydantic models
│       ├── store.ts              # Zustand nav/view state
│       ├── format.ts             # formatting + label helpers
│       └── utils.ts
│
├── backend/                      # ── FastAPI backend ──
│   ├── requirements.txt
│   └── app/
│       ├── main.py               # FastAPI entry (CORS, routers)
│       ├── core/
│       │   ├── config.py         # settings + upload dir
│       │   └── cors.py
│       ├── models/               # Pydantic v2 data models
│       │   ├── document.py       # DocumentMetadata, status, source
│       │   ├── ocr.py            # OcrResult, OcrBlock
│       │   ├── field.py          # EditableExtractionField, FieldType
│       │   ├── template.py       # ExtractionTemplate, TemplateFieldDefinition
│       │   ├── benchmark.py      # BenchmarkRun, BenchmarkMetric, FieldMetric, RunSummary
│       │   └── batch.py          # BatchProcessingResult, BatchItemResult
│       ├── services/             # ── PLACEHOLDER business logic ──
│       │   ├── document_parser.py
│       │   ├── ocr_extraction.py
│       │   ├── ocr_correction.py
│       │   ├── sentence_splitter.py
│       │   ├── chunker.py
│       │   ├── embedding.py
│       │   ├── template_service.py
│       │   ├── template_application.py
│       │   └── benchmark_service.py
│       ├── api/
│       │   ├── router.py
│       │   └── endpoints/
│       │       ├── documents.py
│       │       ├── ocr.py
│       │       ├── templates.py
│       │       ├── batch.py
│       │       └── benchmarks.py
│       └── data/
│           └── mock.py           # in-memory store + rich seed data
│
└── Caddyfile                     # gateway: forwards ?XTransformPort=<port>
```

---

## 2. Running locally

Two processes must run simultaneously: the FastAPI backend (port **8000**) and the
Next.js frontend (port **3000**). The built-in Caddy gateway (port **81**) routes
requests: default traffic → `:3000`, and any request with `?XTransformPort=8000` →
`:8000` (FastAPI). The frontend API client always appends `XTransformPort=8000` to
backend calls, so everything works behind the single exposed gateway.

### Backend (FastAPI)

```bash
cd backend
pip install -r requirements.txt          # fastapi, uvicorn, pydantic, python-multipart
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- Interactive API docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

### Frontend (Next.js)

```bash
# from the project root
bun install        # if not already installed
bun run dev        # starts on http://localhost:3000
bun run lint       # ESLint
```

Open the app via the **Preview Panel** (the gateway on port 81) — do not navigate to
`localhost:3000` directly when validating through the sandbox, because client-side API
calls rely on the gateway's `XTransformPort` forwarding.

---

## 3. The workflow

The app is a single-page experience with a left sidebar that switches between 6 views:

| Step | View | What it does |
|------|------|--------------|
| — | **Dashboard** | Overview: recent docs, templates, latest benchmark, quick actions |
| 1 | **Documents** | Drag-and-drop upload **+** select from the corporate document DB |
| 2 | **Extraction Review** | Side-by-side: document preview (bbox blocks) ↔ editable OCR fields & tables; low-confidence highlighting; Save / Reset / Approve |
| 3 | **Templates** | List templates + visual editor: field definitions, validation rules, OCR method, chunking strategy, looping config; live preview; seed from a reviewed doc |
| 4 | **Apply Template** | Pick a template + documents → animated batch run → results table with per-field Inspect drawer + CSV export |
| 5 | **Benchmarking** | Run benchmarks; metric cards (accuracy, exact match, correction rate, missing fields, latency, consistency, success rate); per-field bar chart + consistency area chart; metrics & field breakdown tables; extraction history |

---

## 4. API endpoints (FastAPI)

All under `/api`. Base URL through the gateway: `http://localhost:81/api/<path>?XTransformPort=8000`.

### Documents
- `GET    /api/documents` — list (filters: `source`, `type`, `collection`, `q`)
- `POST   /api/documents/upload` — multipart upload (`file`)
- `GET    /api/documents/{id}`
- `DELETE /api/documents/{id}`
- `POST   /api/documents/{id}/process` — run OCR/extraction (placeholder)

### OCR / Extraction
- `GET  /api/ocr/{document_id}` — full OCR result with blocks
- `PUT  /api/ocr/{document_id}` — update edited blocks / approve
- `POST /api/ocr/{document_id}/reset` — revert edits
- `GET  /api/ocr/{document_id}/fields` — editable fields derived from blocks

### Templates
- `GET    /api/templates`
- `POST   /api/templates` — create
- `GET    /api/templates/{id}`
- `PUT    /api/templates/{id}`
- `DELETE /api/templates/{id}`

### Batch application
- `POST /api/batch/apply` — `{ template_id, document_ids[] }` → `BatchProcessingResult`
- `GET  /api/batch`
- `GET  /api/batch/{id}`

### Benchmarking
- `GET  /api/benchmarks` — all runs (full)
- `GET  /api/benchmarks/runs` — run summaries (history table)
- `POST /api/benchmarks` — `{ template_id, document_ids[], repeat }` → `BenchmarkRun`
- `GET  /api/benchmarks/{run_id}`

---

## 5. Data models (Pydantic v2)

Defined in `backend/app/models/`. Key models:

- **`DocumentMetadata`** — id, name, type, source (`upload`/`corporate_db`), status, page_count, confidence, tags, collection, timestamps
- **`OcrResult`** — engine, pages, `blocks: OcrBlock[]`, overall_confidence, edited, approved
- **`OcrBlock`** — page, type (text/heading/table/key_value/image/signature), bbox, text, confidence, edited, data
- **`EditableExtractionField`** — label, key, type, value, raw_value, confidence, confidence_level, required, edited, valid, options
- **`ExtractionTemplate`** — name, document_type, `fields: TemplateFieldDefinition[]`, ocr_method, chunking_strategy, max_pages, loop_condition, version, success_rate, usage_count
- **`TemplateFieldDefinition`** — label, key, type, example_value, validation_rule, required, extraction_hint, default_value
- **`BenchmarkRun`** — run_id, template, document_ids, status, `metrics: BenchmarkMetric[]`, `field_metrics: FieldMetric[]`, consistency_samples
- **`BenchmarkMetric`** — name, label, value, unit (`ratio`/`ms`/`count`/`percent`), target
- **`BatchProcessingResult`** — total/done/failed, `items: BatchItemResult[]`, averages

---

## 6. Where to add your custom logic

The backend ships with **placeholder service modules** under `backend/app/services/`.
Each has a clear `TODO` and a suggested library. Replace the stub functions with your
real implementation — the API routes and data models already wire them in.

| Service | File | Implement |
|---------|------|-----------|
| Document parser | `services/document_parser.py` | `parse_document(file_path)` → page count, text layers, metadata. Use `pdfplumber`/`PyMuPDF`, `python-docx`, `Pillow`. |
| OCR extraction | `services/ocr_extraction.py` | `run_ocr(document_id, parsed)` → blocks + confidence. Use `pytesseract`, `paddleocr`, `easyocr`, or a VLM (GLM-4.6V via `z-ai-web-dev-sdk`). |
| OCR correction | `services/ocr_correction.py` | `correct_ocr(blocks)` — clean text, merge/split blocks, recompute confidence levels. |
| Sentence splitter | `services/sentence_splitter.py` | `split_sentences(text)` — `nltk`, `spacy`, or a rule-based splitter. |
| Chunker | `services/chunker.py` | `chunk_document(blocks, strategy, ...)` — page-by-page / fixed-size / semantic / sliding-window. |
| Embedding | `services/embedding.py` | `embed_texts(texts)` — `sentence-transformers`, `openai`, or z-ai embeddings. |
| Template application | `services/template_application.py` | `apply_template_to_document(template_id, document_id)` — the **core deterministic pipeline**: parser → OCR → correction → splitter → chunker → embedding → field matching. Currently returns mock values. |
| Benchmark evaluation | `services/benchmark_service.py` | `run_benchmark(payload)` — compare extracted values against ground truth; compute field-level accuracy, exact match, correction rate, missing count, latency, consistency across repeats. Currently derives mock metrics. |

### Persistence
The data layer is an in-memory store (`backend/app/data/mock.py`) seeded with rich mock
data (8 corporate docs, 1 uploaded sample with OCR blocks, 3 templates, 1 benchmark run).
Swap `store` for a repository backed by your DB of choice (SQLAlchemy / SQLModel /
Tortoise + Postgres/SQLite). The service modules access the store via a lazy `_store()`
accessor to avoid circular imports — keep that pattern when migrating.

### Connecting the frontend to a new backend endpoint
1. Add the Pydantic model in `backend/app/models/`.
2. Add the route in `backend/app/api/endpoints/` and include it in `router.py`.
3. Add the matching TS type in `src/lib/types.ts`.
4. Add an API method in `src/lib/api.ts` (remember: relative path + the client auto-appends `XTransformPort=8000`).

---

## 7. Architecture notes

- **Single exposed port**: only the Caddy gateway (port 81) is external. The Next.js
  app (3000) and FastAPI (8000) are internal. Cross-service browser requests use the
  `XTransformPort` query param so Caddy routes them correctly.
- **Frontend → Backend**: the typed client in `src/lib/api.ts` always calls relative
  paths and appends `XTransformPort=8000`, so it works transparently behind the gateway.
- **State**: TanStack Query for server state; a tiny Zustand store (`src/lib/store.ts`)
  for view navigation and cross-view context handoffs (e.g. "Approve" in OCR Review →
  opens the Template editor pre-seeded with that doc's fields).
- **Theme**: emerald/teal primary on slate neutrals (no indigo/blue), full light/dark
  support via `next-themes`.
- **Responsive + accessible**: semantic landmarks, keyboard-navigable, sticky footer,
  `scrollbar-thin` for long lists, ARIA labels on icon buttons.

---

## 8. Mock data

`backend/app/data/mock.py` seeds:
- 8 corporate documents across collections (construction-reports, finance, procurement, legal, logistics)
- 1 uploaded sample (`Concrete_2s_sample.pdf`) with an 8-block OCR result (headings, key-values, a table) and editable fields
- 3 templates (Construction Test Reports, Invoice Processing, PO Identifier) with full field definitions
- 1 completed benchmark run so the dashboard/benchmarking views are populated on first load

This lets you explore the entire UI immediately without uploading anything.
