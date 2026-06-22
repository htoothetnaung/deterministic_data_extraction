"use client";

import * as React from "react";
import { AppShell } from "@/components/app/app-shell";
import { useNav } from "@/lib/store";
import { DashboardView } from "@/views/dashboard-view";
import { DocumentsView } from "@/views/documents-view";
import { OcrReviewView } from "@/views/ocr-review-view";
import { TemplatesView } from "@/views/templates-view";
import { ApplyTemplateView } from "@/views/apply-template-view";
import { BenchmarkingView } from "@/views/benchmarking-view";

export default function Home() {
  const view = useNav((s) => s.view);

  return (
    <AppShell>
      {view === "dashboard" && <DashboardView />}
      {view === "documents" && <DocumentsView />}
      {view === "ocr-review" && <OcrReviewView />}
      {view === "templates" && <TemplatesView />}
      {view === "apply-template" && <ApplyTemplateView />}
      {view === "benchmarking" && <BenchmarkingView />}
    </AppShell>
  );
}
