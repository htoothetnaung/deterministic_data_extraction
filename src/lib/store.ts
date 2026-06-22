/**
 * Global UI/navigation state for the single-page ExtractIQ app.
 * Holds the active view plus context (selected document/template/etc.)
 * so different views can hand off to each other.
 */
import { create } from "zustand";

export type ViewId =
  | "dashboard"
  | "documents"
  | "ocr-review"
  | "templates"
  | "apply-template"
  | "benchmarking";

interface NavState {
  view: ViewId;
  // context handoffs between views
  reviewDocumentId: string | null;
  applyTemplateId: string | null;
  benchmarkTemplateId: string | null;
  createTemplateFromDocId: string | null;
  // actions
  go: (view: ViewId) => void;
  review: (documentId: string) => void;
  applyTemplate: (templateId: string) => void;
  benchmarkTemplate: (templateId: string) => void;
  createTemplateFrom: (documentId: string) => void;
}

export const useNav = create<NavState>((set) => ({
  view: "dashboard",
  reviewDocumentId: null,
  applyTemplateId: null,
  benchmarkTemplateId: null,
  createTemplateFromDocId: null,
  go: (view) => set({ view }),
  review: (documentId) => set({ view: "ocr-review", reviewDocumentId: documentId }),
  applyTemplate: (templateId) => set({ view: "apply-template", applyTemplateId: templateId }),
  benchmarkTemplate: (templateId) => set({ view: "benchmarking", benchmarkTemplateId: templateId }),
  createTemplateFrom: (documentId) => set({ view: "templates", createTemplateFromDocId: documentId }),
}));
