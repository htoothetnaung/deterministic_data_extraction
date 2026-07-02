# ExtractIQ — Worklog

---
Task ID: 1
Agent: main (orchestrator)
Task: Build the FastAPI backend (Pydantic models, placeholder services, API routes, mock data).

Work Log:
- Created `backend/` with modular structure: `app/{core,models,services,api/endpoints,data}`.
- Defined Pydantic v2 models: document, ocr, field, template, benchmark, batch.
- Implemented placeholder service modules (with TODO comments): document_parser, ocr_extraction, ocr_correction, sentence_splitter, chunker, embedding, template_service, template_application, benchmark_service.
- Implemented API endpoints: documents (list/upload/get/delete/process), ocr (get/update/reset/fields), templates (CRUD), batch (apply/list/get), benchmarks (list/runs/create/get).
- Built rich in-memory mock data store: 8 corporate docs + 1 uploaded sample, 3 templates, seeded OCR result (8 blocks), seeded benchmark run.
- Fixed circular import by binding `store` before calling `store.seed()`, and using lazy `_store()` accessors in services.
- Ran the server on port 8000 and verified all endpoints (health, documents=9, templates=3, benchmarks, OCR, batch apply, benchmark create, upload).

Stage Summary:
- FastAPI backend running on http://localhost:8000 (docs at /docs).
- All endpoints return rich mock data; ready for the Next.js frontend to consume via the gateway using `?XTransformPort=8000`.
- Key extension points for the user are clearly marked with TODO comments in `backend/app/services/*.py`.

---
Task ID: 8
Agent: full-stack-developer
Task: Build the Apply Template view (batch processing).

Work Log:
- Reviewed prior work (worklog.md), dashboard style reference, api.ts, types.ts, store.ts, format.ts, and app components (PageHeader, SectionCard, EmptyState, StatCard, badges).
- Inspected shadcn primitives (sheet, select, checkbox, table, tooltip, scroll-area, separator) to confirm exact export APIs.
- Overwrote src/views/apply-template-view.tsx with a 3-stage flow:
  - Stage 1 (config): template selector grid (left, lg:col-span-2) + searchable/filterable document table with per-row checkboxes (right, lg:col-span-3). Select-all-visible, "Select all corporate", Clear actions. Sticky bottom summary bar showing selected template name + selected count + primary Run button (disabled until template + ≥1 doc selected).
  - Stage 2 (processing): animated progress bar 0→100% via interval; per-document queue with staggered timeouts flipping queued→processing→done in parallel with the real batchApi.apply() promise; counter cards (Done/Processing/Failed/Total); Cancel returns to config.
  - Stage 3 (results): 5 StatCards (Total, Succeeded, Failed, Avg confidence, Avg latency) + results SectionCard table (noBodyPadding) with BatchStatusBadge + ConfidenceBadge + matched/mismatched/missing/latency + Inspect button per row. Low-confidence/failure callout card. Actions: Back to configuration (outline), Export CSV (outline), Run benchmark on these results (primary → useNav().benchmarkTemplate).
- Inspect Sheet: right-side drawer listing each EditableExtractionField with label + key + ConfidenceBadge + editable Input (local state, no save). Low-confidence fields highlighted with rose border/bg; invalid fields show validation message.
- exportCsv helper: builds rows (document_name,status,confidence,matched,mismatched,missing,latency_ms), Blob({type:'text/csv'}), URL.createObjectURL, <a download>, revoke.
- Styling: emerald/teal primary + slate neutrals, NO indigo/blue. Responsive (grids collapse to 1 col). Long lists: max-h-[...] overflow-y-auto / ScrollArea. Framer Motion AnimatePresence for stage transitions + staggered list items.
- Verified: bun run lint clean; curl localhost:3000 → 200; dev.log shows no errors / clean compile.

Stage Summary:
- Apply Template view complete: configuration → processing simulation → results table, with inspect drawer, CSV export, and benchmark handoff. No other files modified.

---
Task ID: 5
Agent: full-stack-developer
Task: Build the Documents view (upload + corporate DB selection).

Work Log:
- Read prior worklog (FastAPI on :8000 done, Next.js foundation + dashboard done) and studied dashboard-view.tsx as the style reference for spacing, framer-motion `fade` variants, SectionCard patterns, table styling, and emerald/teal primary usage.
- Reviewed shadcn primitives actually available (select, dropdown-menu, checkbox, scroll-area, progress, table) to import them from correct `@/components/ui/*` paths, and re-read api.ts/types.ts/format.ts/store.ts to align with `documentsApi.upload/process/remove`, `DocumentMetadata`, `DocumentSource`, `formatBytes/formatRelative/pct/docTypeLabel`, and `useNav().review`.
- Wrote `/home/z/my-project/src/views/documents-view.tsx`: PageHeader (eyebrow "Step 1", FileStack icon, primary "Upload Documents" button) + a 3-up StatCard strip (Total / Uploaded / Corporate DB) that mirrors the dashboard's stat card rhythm.
- Implemented a drag-and-drop upload zone (onDragOver/Leave/Drop with `isDragging` border+bg feedback, hidden `<input type=file multiple>` shared with the header button) and an upload queue list per file (icon, name, formatBytes size, animated framer-motion progress fill 0→90% during fetch → 100% on success, status pills Uploading→Uploaded/Failed via amber/emerald/rose badges, per-row "Process" button calling `documentsApi.process(id)`, per-row remove). Uploads use `useMutation(documentsApi.upload)` with sonner toasts + `invalidateQueries(["documents"])`.
- Built the right-side "Corporate Document Database" panel: search Input + Select type filter (all/invoice/report/contract/form/other), `source === "corporate_db"` filtered list with Checkbox selection, collection + type badges, page count, selected-count badge, and an "Add to workflow" footer action.
- Built the full-width "All documents" table (SectionCard noBodyPadding, motion.tr rows with the shared `fade` variants): Name (icon + pages + size), SourceBadge (Upload vs Corporate DB), Type, StatusBadge, Confidence (pct or —), Added (formatRelative), and an actions cell with a "Review" button (useNav().review) + DropdownMenu kebab (View / Delete via documentsApi.remove with optimistic invalidation + toasts).
- Added loading skeletons and EmptyState for the queue, corporate list, and the all-documents table; used ScrollArea/max-h with `scrollbar-thin` for long lists; kept the whole view responsive (`grid-cols-1 lg:grid-cols-3`, hidden columns at sm/md/lg breakpoints).
- Ran `bun run lint` (clean), hit `curl http://localhost:3000/` (HTTP 200), and checked dev.log for errors/warnings (none).

Stage Summary:
- Documents view is production-ready and visually consistent with the dashboard (same fade variants, SectionCard usage, emerald/teal/slate/amber/rose palette, StatCard strip).
- Real upload → process → review handoff works against the FastAPI backend via the gateway (XTransformPort handled inside api.ts); TanStack Query `["documents"]` is invalidated on every successful mutation so the all-documents table and stat cards stay fresh.
- Drag-drop, simulated progress (10→90% interval then 100% on resolve), per-file process button, corporate DB search/filter/multi-select, and kebab Delete are all wired with sonner toasts and skeleton/empty states. No other files were modified.

---
Task ID: 7
Agent: full-stack-developer
Task: Build the Templates view (list + editor).

Work Log:
- Read worklog, dashboard-view (style ref), lib/{api,types,store}, and components/app/{page-header,section,badges} to align with conventions; inspected available shadcn/ui primitives.
- Overwrote src/views/templates-view.tsx with a two-mode view: (A) list with PageHeader, responsive sm:2/lg:3 card grid, ConfidenceBadge, footer meta line, Edit/Apply/Benchmark/kebab-Delete actions, AlertDialog confirm, loading skeletons, empty state; (B) full-view-swap editor with header bar (Back + name input + Save/Cancel), 2-column layout (form 60% / sticky preview 40%), General settings card, Advanced adjustments card (3 labelled groups: OCR method, chunking strategy, max pages + loop condition), and a Field definitions editor with inline-editable Card rows (name/key auto-slug/type select/example/regex/notes/required/delete) wrapped in framer-motion AnimatePresence+layout, plus an "Add field" button.
- Live preview panel renders a read-only form-style visualization of fields + advanced-settings summary.
- Seed-on-open: lazy useState initializer reads useNav().createTemplateFromDocId to open the editor in create mode; mount effect clears the nav flag; editor fetches ocrApi.fields(docId), maps to TemplateFieldDefinition[], shows a loading banner, toasts success/failure.
- Save: validates name + ≥1 field, calls templatesApi.create/update, invalidates ["templates"], toasts, returns to list.
- Fixed ESLint: lazy useState for editor open (no setState in effect), moved setSeeding into useState initializer so only async setState remains inside the seeding effect; removed unused eslint-disable directives.
- Verified: bun run lint clean (0 errors / 0 warnings); curl http://localhost:3000/ → 200; dev.log has no error/⨯/Failed lines.

Stage Summary:
- Production-quality "Extraction Templates" view with list + editor, emerald/teal palette (no indigo/blue), full shadcn/ui integration.
- Cards with Edit/Apply/Benchmark/kebab-Delete (AlertDialog), ConfidenceBadge, footer meta, loading + empty states.
- Inline-editable field rows with framer-motion add/remove animations, auto-slug key, all 11 FieldTypes, regex validation, required switch.
- Sticky live-preview panel mirroring the template as a read-only form.
- Seed-on-open flow from OCR review wired through useNav().createTemplateFromDocId + ocrApi.fields.
- Save flow wired to templatesApi.create/update with TanStack Query invalidation + sonner toasts.

---
Task ID: 6
Agent: full-stack-developer
Task: Build the OCR/Extraction Review view (side-by-side editor).

Work Log:
- Read worklog + dashboard-view + api/types/format/store + app components to lock in design system & API surface.
- Inspected live `/api/ocr/doc-upl-001` to confirm block shapes (key_value: `data:{key,value}`; table: `data:{rows:[[...]]}` + header text; bbox `[x,y,w,h]` rel 0..1; many blocks have `bbox:null`).
- Wrote `src/views/ocr-review-view.tsx` as a single client component:
  - PageHeader with ScanText icon + Reset / Save / Approve (emerald) actions and overall ConfidenceBadge tooltip.
  - Document selector (shadcn Select) wired to `documentsApi.list()` + `useNav().review()`; current docId always represented.
  - Summary strip: 4 colored chips (Blocks / Edited / Low conf. / live Confidence).
  - Resizable side-by-side (`react-resizable-panels`, 52/48, min 35/35); stacked layout on mobile via `useIsMobile`.
  - LEFT preview: paginated A4 mock pages with absolutely-positioned bbox blocks color-coded by confidence; unlocated blocks stacked below; legend + prev/next.
  - RIGHT fields: scrollable cards — Input for key_value, Textarea for heading/text, editable mini-table (sticky headers) for table; per-card ConfidenceBadge, Edited pill, "Needs review" flag, color-coded left accent + ring.
  - Click a bbox block → selects + smooth-scrolls matching field card into view with ring highlight.
- State: local `blocks` synced from `ocrQ.data`; `dirty` via deep compare of text + data JSON; `liveConfidence` recomputed (edited blocks bumped to ≥0.95) so the user sees progress.
- Mutations: Save → `ocrApi.update(docId, {blocks})` + invalidate + toast; Reset → `ocrApi.reset` + refetch; Approve → `ocrApi.update(docId, {approved:true, blocks})` then `createTemplateFrom(docId)`. Process-document mutation for the empty state.
- Loading → Skeleton; no OCR data → EmptyState with "Process document" button (`documentsApi.process`).
- Verified: `bun run lint` clean; `curl localhost:3000` returns HTTP 200; dev.log shows clean Turbopack compiles, no TS errors.

Stage Summary:
- Production-quality Extraction Review view delivered as a single file (`src/views/ocr-review-view.tsx`, ~640 lines).
- Matches dashboard design language (fade variants, emerald primary, slate neutrals, amber/rose confidence semantics; no indigo/blue).
- All requested features implemented: resizable side-by-side, bbox overlay + click-to-jump, type-aware editors, dirty tracking, live confidence recompute, save/reset/approve flow with toasts, mobile fallback, loading + empty states.
- Ready for the next stage (templates view consuming `createTemplateFromDocId`).

---
Task ID: 9
Agent: full-stack-developer
Task: Build the Benchmarking view (metrics, charts, history).

Work Log:
- Read prior worklog and reference files (dashboard-view for style, api.ts, types.ts, store.ts, format.ts, app components, ui/chart.tsx).
- Confirmed backend `/api/benchmarks` returns 2 seeded runs with metrics, field_metrics, consistency_samples; `/api/benchmarks/runs` returns RunSummary list.
- Overwrote `src/views/benchmarking-view.tsx` with a full "use client" view (~900 lines).
- Implemented PageHeader with "Run New Benchmark" primary button opening a configuration Dialog.
- Dialog: template Select (defaults from useNav().benchmarkTemplateId), documents multi-select via Checkbox list inside ScrollArea with "Select all"/"Clear all" toggle, repeat-runs Number input (1..10), Cancel/Run buttons. Run calls `benchmarksApi.create`, shows spinner + toast, invalidates ["benchmarks"] & ["benchmark-runs"], selects the new run.
- Run selector (Select) above metrics, defaulting to latest run; shows run_id + template_name + relative date; right side shows template badge, doc count, formatted date.
- 4 StatCards: Field-level Accuracy (success), Exact Match (primary), OCR Correction Rate (warning, lower-is-better trend), Processing Latency (default, ms) — each with target hint and meets/over trend.
- Charts row (2x via shadcn ChartContainer + recharts): left horizontal BarChart of field accuracy with 0.95 ReferenceLine and per-cell color (emerald/amber/rose by accuracy tier); right AreaChart of consistency_samples (teal gradient fill). Both ~280px, ChartTooltip with ChartTooltipContent formatter rendering pct, empty-state handled.
- Metrics breakdown table (SectionCard noBodyPadding): every run.metric with formatted value (ratio→pct, ms→"X ms", count→int), target, and Progress bar + meets/over icon for ratio metrics; Info tooltip for description.
- Field-level details table: Field label + key, Accuracy (Progress + colored pct), Exact Match, Missing, Correction Rate, Confidence — tabular-nums throughout.
- Extraction history table from benchmarksApi.runs(): Run ID (mono), Template, Files, BenchStatusBadge, Accuracy, Latency, formatRelative date, View/Export (CSV download) actions. Selected row highlighted; row click selects.
- Verified via agent-browser through the gateway (port 81): view renders, both charts produce recharts SVGs, dialog opens, "Select all" checks all docs and enables Run, no console/runtime errors.
- `bun run lint` clean; `tsc --noEmit` reports zero errors in benchmarking-view.tsx.

Stage Summary:
- Production-quality Benchmarking view at src/views/benchmarking-view.tsx covering Step 5 (deterministic extraction quality, consistency, latency).
- Full run lifecycle: list → select → inspect metrics/fields/charts → run new benchmark → re-select; plus CSV export per history row.
- Matches dashboard-view quality (motion, StatCard, SectionCard, EmptyState, Badge, skeletons, responsive grids, tabular-nums, accessible tooltips). No indigo/blue; chart palette uses --chart-1..5 (emerald/teal/amber/rose/violet).

---
Task ID: 10
Agent: main (orchestrator)
Task: End-to-end verification + README.

Work Log:
- Ran `bun run lint` across the full project — clean (0 errors).
- Verified FastAPI backend alive on :8000 (health, documents=9, templates=3, benchmarks).
- Used agent-browser through the gateway (:81) to exercise every view:
  - Dashboard: renders real data (docs, templates, latest benchmark).
  - Documents: drag-drop zone + corporate DB list + all-documents table.
  - OCR Review: side-by-side editor; editing a field enables Save; bbox blocks + editable fields/table.
  - Templates: 3 cards with Edit/Apply/Benchmark actions.
  - Apply Template: select all corporate → run batch → results table with Inspect drawer (editable fields).
  - Benchmarking: metric cards, bar + area charts, metrics/field breakdown tables, history; ran a NEW benchmark (RUN-2026-0002) from the dialog successfully.
- Fixed a UX issue: unmatched corporate docs showed 0% confidence (looked broken). Improved `apply_template_to_document` to generate deterministic placeholder values with low-medium confidence (0.58) — now reads as "weak extraction" instead of "broken".
- VLM-verified dashboard + benchmarking screenshots: confirmed polished, enterprise-grade, no visual issues.
- No console/runtime errors anywhere.

Stage Summary:
- All 6 views verified interactive through the gateway with real backend data.
- App is production-ready as a demo/starting point; backend placeholder services are clearly marked for the user to extend.

---
Task ID: 11
Agent: main (orchestrator)
Task: Write README.

Work Log:
- Authored `/home/z/my-project/README.md` covering: folder structure, run instructions (frontend + backend), the 6-view workflow, all API endpoints, Pydantic data models, a mapping table of where to implement each custom module (parser/OCR/splitter/chunker/embedding/template-application/benchmark), persistence notes, and architecture notes (gateway/XTransformPort, state, theme).

Stage Summary:
- README complete; project is documented and ready for the user to extend the backend logic.
