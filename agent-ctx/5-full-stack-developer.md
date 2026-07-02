# Task 5 — Documents View (full-stack-developer)

## Scope
Overwrite `/home/z/my-project/src/views/documents-view.tsx` with a complete, production-quality "Documents" view (Step 1 of the ExtractIQ workflow). No other files modified.

## What was built
- **PageHeader**: eyebrow "Step 1", title "Documents", `FileStack` icon, primary "Upload Documents" button that triggers the hidden file input.
- **StatCard strip** (3 cards): Total documents / Uploaded / Corporate DB — mirrors dashboard's stat-card rhythm and stays in sync with the `["documents"]` query.
- **Two-column grid (`lg:grid-cols-3`)**:
  - **Left (`lg:col-span-2`)**: dashed drag-and-drop zone (`onDragOver/Leave/Drop`, `isDragging` visual feedback, hidden `<input type=file multiple>` shared with header button), and an upload queue list with per-file icon/name/`formatBytes` size, animated framer-motion progress fill (simulated 10→90% interval then 100% on resolve), status pills (Uploading→Uploaded/Failed), a "Process" button calling `documentsApi.process(id)` that appears after success, and per-row remove.
  - **Right**: "Corporate Document Database" panel — search Input, Select type filter (all/invoice/report/contract/form/other), filtered `source === "corporate_db"` list with Checkbox selection, collection + type badges, page count, selected-count badge, "Add to workflow" footer action.
- **Full-width "All documents" table** (`SectionCard noBodyPadding`, `motion.tr` rows with shared `fade` variants): Name (+icon+pages+size), SourceBadge (Upload vs Corporate DB), Type, StatusBadge, Confidence (pct or —), Added (formatRelative), actions cell with "Review" button (`useNav().review`) + DropdownMenu kebab (View / Delete via `documentsApi.remove`).
- Loading skeletons + EmptyState for queue, corporate list, and table; long lists use ScrollArea/max-h with `scrollbar-thin`.

## APIs used
- `documentsApi.list/upload/process/remove` via `@tanstack/react-query` (`useQuery` for list, `useMutation` for upload/process/delete with `qc.invalidateQueries({queryKey:["documents"]})` on success).
- `useNav().review(id)` for the OCR review handoff.
- `formatBytes`, `formatRelative`, `pct`, `docTypeLabel` from `@/lib/format`.
- Sonner toasts on every upload/process/delete success/error.

## Verification
- `bun run lint` → clean (no errors).
- `curl http://localhost:3000/` → HTTP 200.
- `grep -iE "error|⨯|warn" /home/z/my-project/dev.log` → no matches.

## Notes for downstream agents
- The view reuses the `fade` variants pattern from `dashboard-view.tsx` (custom index delay). Keep this consistent.
- The hidden `<input type=file multiple>` is rendered once at the top of the view and is shared by the PageHeader button, the dropzone, and the empty-state "Upload documents" button via a single `fileInputRef`.
- Progress simulation uses a `Record<string, ReturnType<typeof setInterval>>` ref keyed by tempId; intervals are cleared on success/error/unmount.
- StatCard tone="primary" uses `bg-primary/10 text-primary` (emerald-teal in this app's globals.css) — consistent with the no-indigo/no-blue rule.
