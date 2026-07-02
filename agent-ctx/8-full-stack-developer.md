# Task 8 — Apply Template View (batch processing)

**Agent:** full-stack-developer

## Work Log
- Read worklog.md and prior context (dashboard style, api.ts, types.ts, store.ts, format.ts, app components).
- Inspected shadcn ui components (sheet, select, checkbox, table, tooltip, scroll-area, separator) for correct import APIs.
- Overwrote `src/views/apply-template-view.tsx` with a 3-stage flow:
  - **Stage 1 (config):** template selector card grid (left) + searchable/filterable document table with checkboxes (right). Sticky summary bar at the bottom showing selected template + count + Run button.
  - **Stage 2 (processing):** animated progress bar (0→100%), per-document queue with staggered sim status pills (queued→processing→done), counters (Done/Processing/Failed/Total), Cancel button.
  - **Stage 3 (results):** 5 StatCards (Total/Succeeded/Failed/AvgConfidence/AvgLatency) + per-document results table with status/confidence/matched/mismatched/missing/latency + Inspect button. Low-confidence/failure callout card. Actions: Back to config (outline), Export CSV (outline), Run benchmark on these results (primary).
- **Inspect Sheet:** right-side drawer showing each extracted `EditableExtractionField` as a label + ConfidenceBadge + editable Input (local state, no save). Low-confidence fields highlighted with rose border/bg. Validation messages shown when invalid.
- **ExportCsv helper:** builds CSV from items (document_name,status,confidence,matched,mismatched,missing,latency_ms), Blob + URL.createObjectURL + `<a download>` + revoke.
- Uses emerald/teal primary + slate neutrals. NO indigo/blue. Responsive grids. Long lists use `max-h-[…] overflow-y-auto`. Framer Motion AnimatePresence for stage transitions and staggered cards.
- `bun run lint` — clean, no errors.
- `curl http://localhost:3000/` returns 200; dev.log shows no errors / clean compile.

## Stage Summary
- Apply Template view fully implemented: configuration → processing simulation → results, with inspect drawer, CSV export, and benchmark handoff.
- No other files modified.
