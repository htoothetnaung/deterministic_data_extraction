"use client";

import * as React from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

export function StatCard({
  label,
  value,
  hint,
  icon,
  trend,
  tone = "default",
  className,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  icon?: React.ReactNode;
  trend?: { value: string; direction: "up" | "down" | "flat"; good?: boolean };
  tone?: "default" | "primary" | "success" | "warning" | "danger";
  className?: string;
}) {
  const toneRing = {
    default: "",
    primary: "ring-primary/15",
    success: "ring-emerald-500/15",
    warning: "ring-amber-500/15",
    danger: "ring-rose-500/15",
  }[tone];

  const iconTone = {
    default: "bg-muted text-muted-foreground",
    primary: "bg-primary/10 text-primary",
    success: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    warning: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    danger: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
  }[tone];

  return (
    <Card
      className={cn(
        "relative overflow-hidden p-5 ring-1 ring-inset ring-border/60 transition-shadow hover:shadow-md",
        toneRing,
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1.5">
          <p className="text-sm font-medium text-muted-foreground">{label}</p>
          <p className="text-2xl font-semibold tracking-tight tabular-nums">
            {value}
          </p>
          {hint ? (
            <p className="text-xs text-muted-foreground">{hint}</p>
          ) : null}
        </div>
        {icon ? (
          <div
            className={cn(
              "flex size-10 shrink-0 items-center justify-center rounded-lg",
              iconTone,
            )}
          >
            {icon}
          </div>
        ) : null}
      </div>
      {trend ? (
        <div className="mt-3 flex items-center gap-1.5 text-xs">
          {trend.direction === "up" ? (
            <TrendingUp
              className={cn(
                "size-3.5",
                trend.good ? "text-emerald-600" : "text-rose-600",
              )}
            />
          ) : trend.direction === "down" ? (
            <TrendingDown
              className={cn(
                "size-3.5",
                trend.good ? "text-emerald-600" : "text-rose-600",
              )}
            />
          ) : (
            <Minus className="size-3.5 text-muted-foreground" />
          )}
          <span
            className={cn(
              "font-medium tabular-nums",
              trend.direction === "flat"
                ? "text-muted-foreground"
                : trend.good
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-rose-600 dark:text-rose-400",
            )}
          >
            {trend.value}
          </span>
        </div>
      ) : null}
    </Card>
  );
}
