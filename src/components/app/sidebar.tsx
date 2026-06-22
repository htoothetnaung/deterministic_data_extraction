"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { useNav, type ViewId } from "@/lib/store";
import {
  LayoutDashboard,
  FileStack,
  ScanText,
  FileCode2,
  Layers,
  Gauge,
  Sparkles,
  Moon,
  Sun,
  Search,
  CircleHelp,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface NavItem {
  id: ViewId;
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  step?: number;
}

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Workflow",
    items: [
      {
        id: "dashboard",
        label: "Dashboard",
        description: "Overview & quick actions",
        icon: LayoutDashboard,
      },
      {
        id: "documents",
        label: "Documents",
        description: "Upload & select source documents",
        icon: FileStack,
        step: 1,
      },
      {
        id: "ocr-review",
        label: "Extraction Review",
        description: "Edit OCR output & fields",
        icon: ScanText,
        step: 2,
      },
      {
        id: "templates",
        label: "Templates",
        description: "Create reusable extraction templates",
        icon: FileCode2,
        step: 3,
      },
      {
        id: "apply-template",
        label: "Apply Template",
        description: "Batch extraction across documents",
        icon: Layers,
        step: 4,
      },
      {
        id: "benchmarking",
        label: "Benchmarking",
        description: "Measure deterministic performance",
        icon: Gauge,
        step: 5,
      },
    ],
  },
];

export function Sidebar() {
  const view = useNav((s) => s.view);
  const go = useNav((s) => s.go);

  return (
    <aside className="flex h-full w-full flex-col bg-sidebar text-sidebar-foreground">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-5 py-5">
        <div className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
          <Sparkles className="size-5" />
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold tracking-tight">ExtractIQ</p>
          <p className="text-[11px] text-muted-foreground">
            Deterministic Extraction
          </p>
        </div>
      </div>

      {/* Quick search (decorative) */}
      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search…"
            className="h-8 bg-background/60 pl-8 text-xs"
          />
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-6 overflow-y-auto scrollbar-thin px-3 py-3">
        {NAV.map((group) => (
          <div key={group.section}>
            <p className="px-2 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70">
              {group.section}
            </p>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = view === item.id;
                const Icon = item.icon;
                return (
                  <li key={item.id}>
                    <button
                      onClick={() => go(item.id)}
                      className={cn(
                        "group relative flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-sm transition-colors",
                        active
                          ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                          : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
                      )}
                    >
                      {active ? (
                        <span className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-primary" />
                      ) : null}
                      <Icon
                        className={cn(
                          "size-4 shrink-0",
                          active
                            ? "text-primary"
                            : "text-muted-foreground group-hover:text-foreground",
                        )}
                      />
                      <span className="flex-1 truncate">{item.label}</span>
                      {item.step ? (
                        <span
                          className={cn(
                            "flex size-5 items-center justify-center rounded-full text-[10px] font-semibold tabular-nums",
                            active
                              ? "bg-primary text-primary-foreground"
                              : "bg-muted text-muted-foreground",
                          )}
                        >
                          {item.step}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-sidebar-border px-3 py-3">
        <div className="flex items-center gap-2 rounded-lg px-2.5 py-2">
          <div className="flex size-8 items-center justify-center rounded-full bg-primary/10 text-primary text-xs font-semibold">
            EX
          </div>
          <div className="min-w-0 flex-1 leading-tight">
            <p className="truncate text-xs font-medium">Extraction Team</p>
            <p className="truncate text-[11px] text-muted-foreground">
              Enterprise workspace
            </p>
          </div>
          <Button variant="ghost" size="icon" className="size-7" aria-label="Help">
            <CircleHelp className="size-4" />
          </Button>
        </div>
      </div>
    </aside>
  );
}

export function Topbar({ title, breadcrumb }: { title: string; breadcrumb?: string }) {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border/70 bg-background/80 px-4 backdrop-blur-md sm:px-6">
      <div className="flex min-w-0 items-center gap-2 text-sm">
        {breadcrumb ? (
          <span className="truncate text-muted-foreground">{breadcrumb}</span>
        ) : null}
        <span className="text-muted-foreground/50">/</span>
        <span className="truncate font-medium">{title}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <Button
          variant="ghost"
          size="icon"
          aria-label="Toggle theme"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          className="size-8"
        >
          {mounted ? (
            theme === "dark" ? (
              <Sun className="size-4" />
            ) : (
              <Moon className="size-4" />
            )
          ) : (
            <Sun className="size-4" />
          )}
        </Button>
      </div>
    </header>
  );
}
