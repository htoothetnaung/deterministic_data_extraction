/**
 * Global UI/navigation state for the single-page Atenxion app.
 * Holds the active lab view for the focused deterministic extraction app.
 */
import { create } from "zustand";

export type ViewId = "extraction-lab" | "parser-lab";

interface NavState {
  view: ViewId;
  go: (view: ViewId) => void;
}

export const useNav = create<NavState>((set) => ({
  view: "extraction-lab",
  go: (view) => set({ view }),
}));
