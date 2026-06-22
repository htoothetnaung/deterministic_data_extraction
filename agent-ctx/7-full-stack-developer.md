# Task 7 — Templates View (list + editor)

**Agent:** full-stack-developer
**File modified:** `src/views/templates-view.tsx`

## Work Log

1. Read `worklog.md`, `dashboard-view.tsx` (style reference), `lib/api.ts`, `lib/types.ts`, `lib/store.ts`, and `components/app/{page-header,section,badges}.tsx` to learn the project's APIs and visual conventions.
2. Inspected the available shadcn/ui components (Select, AlertDialog, DropdownMenu, Switch, Tooltip, etc.) to confirm prop shapes.
3. Overwrote `src/views/templates-view.tsx` with a complete view containing two modes:
   - **Mode A — Template list:** `PageHeader` (title "Extraction Templates", `FileCode2` icon, "Create Template" action), responsive `sm:2 / lg:3` grid of template cards. Each card shows icon, name, 2-line clamped description, a `ConfidenceBadge` (high≥0.9, med≥0.75, low else) or "Unrated" badge, a footer line `{fields} fields · {usage_count} runs · v{version}`, and a footer action row with Edit / Apply (calls `useNav().applyTemplate`) / Benchmark (calls `useNav().benchmarkTemplate`) plus a kebab `DropdownMenu` whose Delete item opens an `AlertDialog` confirmation that runs `templatesApi.remove` and invalidates `["templates"]`. Loading skeletons and an `EmptyState` are handled.
   - **Mode B — Template editor:** Full-view swap with a 2-column layout (form `lg:col-span-3` / preview `lg:col-span-2`). Header bar with Back button, name `Input`, and Save / Cancel. Sections:
     - **General settings** — Name (required), Description (Textarea), Document Type (Select).
     - **Advanced adjustments** — three labelled groups (Document Handling → OCR method; Document Chunking → chunking strategy; Looping Mechanism → max pages + loop condition) with `Separator` dividers and section icons.
     - **Field definitions editor** — list of `Card` rows, each with Field name / Key (auto-suggested from label via `slugify`) / Type (Select over all 11 FieldTypes) / Example value / Validation rule (regex) / Notes / Required Switch / delete button / drag handle. "Add field" buttons (header action + dashed full-width footer button). Rows wrapped in `framer-motion` `AnimatePresence` + `layout` for smooth add/remove animations.
     - **Live preview** (sticky right column) — name, type badge, optional "Seeded from doc" badge, description, a scrollable form-style preview of all fields (label + example value + type chip + regex), and an advanced-settings summary `<dl>`.
4. **Seed-on-open:** When `useNav().createTemplateFromDocId` is set on mount (user came from OCR review "Approve → create template"), the editor opens in create mode immediately via a lazy `useState` initializer, then a mount `useEffect` clears the nav flag. The `TemplateEditor` fetches `ocrApi.fields(docId)` and maps the results into `TemplateFieldDefinition[]` (capped at 25), shows a loading banner while seeding, and toasts success/failure.
5. **Save:** builds a `TemplateCreate` payload, validates name + ≥1 field, calls `templatesApi.create` (new) or `templatesApi.update` (edit), invalidates `["templates"]`, toasts, and returns to list mode.
6. Fixed ESLint issues: replaced `setEditor` inside `useEffect` with a lazy `useState` initializer; moved the seeding `setSeeding(true)` into the `useState` initializer (only async `setState` calls remain inside the effect, which the `react-hooks/set-state-in-effect` rule allows); removed unused `eslint-disable` directives.
7. Verified: `bun run lint` is clean (0 errors, 0 warnings); `curl -s http://localhost:3000/` returns HTTP 200; `dev.log` contains no `error` / `⨯` / `Failed` lines.

## Stage Summary

- Production-quality Templates view with list + editor modes, all using the project's existing shadcn/ui components and the emerald/teal primary palette (no indigo/blue).
- Cards, AlertDialog delete confirmation, kebab menus, inline-editable field rows with framer-motion animations, and a sticky live-preview panel.
- Seed-on-open flow wired to the OCR review handoff via `useNav().createTemplateFromDocId` and `ocrApi.fields`.
- Save flow wired to `templatesApi.create/update` with TanStack Query cache invalidation and sonner toasts.
- Lint clean; dev server compiles and serves the page without errors.
