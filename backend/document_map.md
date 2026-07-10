# Backend Document Map

This document provides a comprehensive mapping of the backend codebase, describing the architecture, layers, and the purpose of each file.

---

## 1. Database Layer (Repositories & Models)

**What it does**: Handles direct queries, updates, and indexing for cases, documents, jobs, and vector evidence in PostgreSQL/pgvector.

* **[engine.py](app/db/engine.py)**: Configures the asynchronous SQLAlchemy engine, session factories, connection pools, and database availability health checks.
* **[models.py](app/db/models.py)**: Declares all SQLAlchemy database schemas (Cases, Documents, Jobs, Evidence Items, and Field Results) and sets up HNSW/pgvector search indexing.
* **[base.py](app/db/repositories/base.py)**: Generic repository base class providing core asynchronous CRUD wrappers (add, get, get_multi, delete).
* **[case_repo.py](app/db/repositories/case_repo.py)**: Manages case folder creation, dashboard listing, and progress tracking telemetry.
* **[document_repo.py](app/db/repositories/document_repo.py)**: Tracks parsing states of documents, manages deduplication checksum logs, and handles claiming/updating background parser tasks.
* **[evidence_repo.py](app/db/repositories/evidence_repo.py)**: Implements dense vector searches, sparse full-text searches (FTS), and hybrid Reciprocal Rank Fusion (RRF) algorithms.
* **[job_repo.py](app/db/repositories/job_repo.py)**: Handles creation and auditing of background parsing/extraction job status codes and execution pricing models.

---

## 2. Core Service Pipelines

**What it does**: Manages the business logic orchestration and asynchronous workers.

* **[production_pipeline.py](app/services/production_pipeline.py)**: Orchestrates the document parsing and vector indexing pipeline, executing Mistral OCR, saving page transcripts, cleaning layouts, and storing chunk embeddings.
* **[production_extraction.py](app/services/production_extraction.py)**: Drives production RAG case extractions by calling retrieval search layers, assembling context prompts, and validating values against schemas.
* **[worker.py](app/services/worker.py)**: Background queue processor daemon that polls enqueued document tasks and runs the parsing and vector indexing pipeline.

---

## 3. Agentic Extraction Engine

**What it does**: Drives iterative planning, retrieval, and validation loops to satisfy strict schema constraints.

* **[agentic_controller.py](app/extraction/agentic_controller.py)**: Coordinates the iterative planning, extraction, and retry loops to fulfill case extraction requirements.
* **[planner.py](app/extraction/planner.py)**: Organizes target extraction schedules and field dependency hierarchies.
* **[field_extractor.py](app/extraction/field_extractor.py)**: Directly interacts with LLMs to pull candidates and reference citations for individual fields.
* **[progressive_retrieval.py](app/extraction/progressive_retrieval.py)**: Dynamically expands vector/search query ranges to locate missing field values.
* **[schema_constrained_extractor.py](app/extraction/schema_constrained_extractor.py)**: Enforces structured formatting and data types on raw model completions.
* **[candidate_resolver.py](app/extraction/candidate_resolver.py)**: Consolidates competing candidates extracted across multiple pages and sources.
* **[validator.py](app/extraction/validator.py)**: Performs post-extraction checks to ensure retrieved values conform to validation rules.
* **[document_map.py](app/extraction/document_map.py)**: Indexes structural parts of documents (titles, pages, markdown table structures) for RAG context mappings.
* **[evidence_pack.py](app/extraction/evidence_pack.py)**: Packs raw chunks and coordinates into structured objects passed to prompt engines.
* **[context_budget.py](app/extraction/context_budget.py)**: Limits content payload lengths to protect the model's token limits.
* **[cost_tracker.py](app/extraction/cost_tracker.py)**: Calculates financial API costs of LLM extraction attempts.
* **[prompts.py](app/extraction/prompts.py)**: Stores prompting context instructions.

---

## 4. Extraction Lab Sandbox Services

**What it does**: Handles experimental runs, fast benchmarks, schema generation tools, and history telemetry reports.

* **[extraction_lab.py](app/services/extraction_lab.py)**: Runs sandboxed extractions on inputs, structures dynamic schema templates, caches run histories, and generates polished markdown summary reports.
* **[extraction_platform.py](app/services/extraction_platform.py)**: Implements local fallback mock storage (cases, schemas, upload configurations) when database access is disabled.

---

## 5. Text Utilities & Cleaners

**What it does**: Cleans, parses, splits, and refactors document texts.

* **[evidence_cleaner.py](app/services/evidence_cleaner.py)**: Deduplicates layout blocks, reformats table tags, normalizes clean texts, and flags financial review tables.
* **[chunker.py](app/services/chunker.py)**: Splits raw document texts into RAG chunks by page boundary, layout structures, or sliding token counts.

---

## 6. API Route Endpoints (FastAPI Controller Definitions)

**What it does**: Handles HTTP network entries and controller delegations.

* **[cases.py](app/api/endpoints/cases.py)**: Exposes endpoints to create, fetch, progress-poll, upload documents to, and trigger index maps for Case bundles.
* **[documents.py](app/api/endpoints/documents.py)**: Exposes endpoints to search, upload, delete, and view layout evidence chunks of individual document uploads.
* **[extraction.py](app/api/endpoints/extraction.py)**: Coordinates Case RAG search hits, extraction jobs trigger/telemetry, candidate lists, and field re-extraction retries.
* **[extraction_lab.py](app/api/endpoints/extraction_lab.py)**: Manages sandbox inputs uploads, schema generation tools, experimental run triggers, cost history tables, and caching cleanup routes.
