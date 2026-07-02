# Task 6 — full-stack-developer — OCR/Extraction Review view

## Goal
Build the production-quality side-by-side "Extraction Review" view (Step 2 of the ExtractIQ workflow)
at `src/views/ocr-review-view.tsx`.

## Work log
1. Read `worklog.md` + dashboard-view + api.ts + types.ts + format.ts + store.ts + page-header/section/badges
   to learn the established design system and APIs.
2. Inspected live OCR + documents endpoints to verify block shapes:
   - key_value blocks carry `data: {key, value}`
   - table blocks carry `data: {rows: [[...]]}` + a `text` header line ("Col1 | Col2 | Col3")
   - bbox is `[x, y, w, h]` relative 0..1; most mock blocks have `bbox: null`
3. Authored the full view (`src/views/ocr-review-view.tsx`) with:
   - PageHeader (ScanText icon, eyebrow "Step 2 · Review")
   - Action cluster: ConfidenceBadge (overall, with Tooltip), Reset / Save / Approve (emerald)
   - Document selector (shadcn Select) populated from `documentsApi.list()`; falls back to a
     synthetic entry if the active docId isn't in the list
   - Summary strip: 4 colored chips — Blocks / Edited / Low conf. / live Confidence
   - Resizable side-by-side area (52/48 split, min 35/35) using `react-resizable-panels`;
     stacked vertically on `<md` via `useIsMobile`
   - LEFT panel: paginated A4 page mockup with absolutely-positioned bbox blocks colored by
     confidence (emerald/amber/rose); unlocated blocks stacked below in a dashed "Unlocated text"
     tray; legend; prev/next page controls
   - RIGHT panel: scrollable field cards keyed by block type — Input for `key_value`, Textarea for
     `heading`/`text`, editable mini-table for `table` (with sticky headers); per-card confidence
     badge, "Edited" pill, "Needs review" flag for low-confidence, color-coded left accent + ring
4. Editing flow:
   - Local `blocks` state synced from `ocrQ.data` via `useEffect`
   - `updateBlockText` / `updateKeyValue` (rewrites `text` + `data`) / `updateTableCell` (mutates
     `data.rows`) — all set `edited: true`
   - `dirty` derived by deep-comparing current blocks vs original server blocks (text + data JSON)
   - `liveConfidence` recomputes the mean (edited blocks bumped to ≥0.95) — visible feedback
   - Save → `ocrApi.update(docId, { blocks })` + invalidate; Reset → `ocrApi.reset`; Approve →
     `ocrApi.update(docId, { approved: true, blocks })` then `createTemplateFrom(docId)`
   - Clicking a bbox block selects it and smooth-scrolls the matching field card into view with a
     brief ring highlight
5. Loading state uses Skeleton; missing OCR result shows an EmptyState with a "Process document"
   button calling `documentsApi.process(docId)`.
6. Ran `bun run lint` (clean) and triggered a Turbopack compile via `curl localhost:3000` —
   HTTP 200, no errors in `dev.log`.

## Stage summary
- File written: `src/views/ocr-review-view.tsx` (~640 lines, single export `OcrReviewView`).
- Design matches dashboard: same fade variants, emerald primary, slate neutrals, amber/rose
  semantic colors for confidence. No indigo/blue anywhere.
- Fully type-safe, lint-clean, compiles cleanly under Turbopack.
- All requested UX features implemented: side-by-side resizable preview/fields, bbox color-coded
  overlay, click-to-jump, type-aware editors (key_value/table/text), live confidence recompute,
  dirty tracking, save/reset/approve flow, mobile fallback, loading + empty states.
