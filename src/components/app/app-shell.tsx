"use client";

import * as React from "react";
import { Sidebar, Topbar } from "./sidebar";
import { useNav } from "@/lib/store";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { Menu } from "lucide-react";

const TITLES: Record<string, { title: string; breadcrumb: string }> = {
  "extraction-lab": { title: "Extraction Lab", breadcrumb: "Deterministic Extraction" },
  "parser-lab": { title: "Parse Lab", breadcrumb: "Document Evidence" },
};

export function AppShell({ children }: { children: React.ReactNode }) {
  const view = useNav((s) => s.view);
  const meta = TITLES[view] ?? { title: "Atenxion", breadcrumb: "" };
  const [mobileOpen, setMobileOpen] = React.useState(false);

  return (
    <div className="flex min-h-screen w-full bg-background">
      <div className="hidden w-64 shrink-0 border-r border-sidebar-border lg:block">
        <div className="sticky top-0 h-screen">
          <Sidebar />
        </div>
      </div>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border/70 bg-background/80 px-3 backdrop-blur-md lg:hidden">
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="size-9">
                <Menu className="size-5" />
              </Button>
            </SheetTrigger>
            <span className="text-sm font-medium">{meta.title}</span>
          </header>
          <Topbar title={meta.title} breadcrumb={meta.breadcrumb} />
          <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8">{children}</main>
          <Footer />
        </div>
        <SheetContent side="left" className="w-72 p-0">
          <Sidebar />
        </SheetContent>
      </Sheet>
    </div>
  );
}

function Footer() {
  return (
    <footer className="mt-auto border-t border-border/60 bg-background/60 px-4 py-4 sm:px-6 lg:px-8">
      <div className="flex flex-col items-center justify-between gap-2 text-xs text-muted-foreground sm:flex-row">
        <p>Atenxion - Deterministic parser-first data extraction.</p>
        <p className="flex items-center gap-3">
          <span>Backend: FastAPI</span>
          <span className="text-border">/</span>
          <span>Frontend: Next.js</span>
        </p>
      </div>
    </footer>
  );
}
