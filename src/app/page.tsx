"use client";

import * as React from "react";
import { AppShell } from "@/components/app/app-shell";
import { useNav } from "@/lib/store";
import { ExtractionLabView } from "@/views/extraction-lab-view";
import { ParserLabView } from "@/views/parser-lab-view";

export default function Home() {
  const view = useNav((s) => s.view);

  return (
    <AppShell>
      {view === "extraction-lab" && <ExtractionLabView />}
      {view === "parser-lab" && <ParserLabView />}
    </AppShell>
  );
}
