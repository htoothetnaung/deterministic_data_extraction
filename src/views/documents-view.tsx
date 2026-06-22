"use client";

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import {
  FileStack,
  Upload,
  UploadCloud,
  FileText,
  Trash2,
  Search,
  Filter,
  Plus,
  Database,
  Loader2,
  CheckCircle2,
  ArrowRight,
  MoreHorizontal,
  Play,
} from "lucide-react";

import { PageHeader } from "@/components/app/page-header";
import { StatCard } from "@/components/app/stat-card";
import { SectionCard, EmptyState } from "@/components/app/section";
import { StatusBadge, Badge } from "@/components/app/badges";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";

import { documentsApi } from "@/lib/api";
import { useNav } from "@/lib/store";
import { formatBytes, formatRelative, pct, docTypeLabel } from "@/lib/format";
import type { DocumentMetadata, DocumentSource } from "@/lib/types";

/* --------------------------- Local types --------------------------- */

type QueueStatus = "uploading" | "uploaded" | "failed";

interface UploadQueueItem {
  tempId: string;
  file: File;
  status: QueueStatus;
  progress: number;
  ackId: string | null;
  ackMessage: string | null;
  error: string | null;
}

/* --------------------------- Helpers --------------------------- */

const fade = {
  hidden: { opacity: 0, y: 8 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.04, duration: 0.3, ease: "easeOut" as const },
  }),
};

const TYPE_FILTERS: { value: string; label: string }[] = [
  { value: "all", label: "All types" },
  { value: "invoice", label: "Invoices" },
  { value: "report", label: "Reports" },
  { value: "contract", label: "Contracts" },
  { value: "form", label: "Forms" },
  { value: "other", label: "Other" },
];

function SourceBadge({ source }: { source: DocumentSource }) {
  if (source === "corporate_db") {
    return (
      <Badge tone="teal">
        <Database className="size-3" />
        Corporate DB
      </Badge>
    );
  }
  return (
    <Badge tone="slate">
      <Upload className="size-3" />
      Upload
    </Badge>
  );
}

function QueueStatusPill({ status }: { status: QueueStatus }) {
  if (status === "uploading") {
    return (
      <Badge tone="amber">
        <Loader2 className="size-3 animate-spin" />
        Uploading
      </Badge>
    );
  }
  if (status === "uploaded") {
    return (
      <Badge tone="emerald">
        <CheckCircle2 className="size-3" />
        Uploaded
      </Badge>
    );
  }
  return (
    <Badge tone="rose">
      <Trash2 className="size-3" />
      Failed
    </Badge>
  );
}

/* --------------------------- View --------------------------- */

export function DocumentsView() {
  const review = useNav((s) => s.review);
  const qc = useQueryClient();

  const docsQ = useQuery({
    queryKey: ["documents"],
    queryFn: () => documentsApi.list(),
  });

  const docs = docsQ.data ?? [];
  const corporateDocs = docs.filter((d) => d.source === "corporate_db");
  const uploadedDocs = docs.filter((d) => d.source === "upload");

  /* ---- Upload queue ---- */
  const [queue, setQueue] = React.useState<UploadQueueItem[]>([]);
  const [isDragging, setIsDragging] = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const intervalsRef = React.useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const uploadMutation = useMutation({
    mutationFn: (file: File) => documentsApi.upload(file),
  });

  const processMutation = useMutation({
    mutationFn: (id: string) => documentsApi.process(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => documentsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  React.useEffect(() => {
    const ivs = intervalsRef.current;
    return () => {
      Object.values(ivs).forEach((iv) => clearInterval(iv));
    };
  }, []);

  const updateQueueItem = (tempId: string, patch: Partial<UploadQueueItem>) => {
    setQueue((prev) =>
      prev.map((it) => (it.tempId === tempId ? { ...it, ...patch } : it)),
    );
  };

  const clearQueueInterval = (tempId: string) => {
    if (intervalsRef.current[tempId]) {
      clearInterval(intervalsRef.current[tempId]);
      delete intervalsRef.current[tempId];
    }
  };

  const removeQueueItem = (tempId: string) => {
    clearQueueInterval(tempId);
    setQueue((prev) => prev.filter((it) => it.tempId !== tempId));
  };

  const handleFiles = (files: FileList | File[]) => {
    const incoming = Array.from(files);
    if (incoming.length === 0) return;

    for (const file of incoming) {
      const tempId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
      const item: UploadQueueItem = {
        tempId,
        file,
        status: "uploading",
        progress: 0,
        ackId: null,
        ackMessage: null,
        error: null,
      };
      setQueue((prev) => [...prev, item]);

      // Simulate progress that fills toward 90% while awaiting the actual fetch.
      intervalsRef.current[tempId] = setInterval(() => {
        setQueue((prev) =>
          prev.map((it) => {
            if (it.tempId !== tempId || it.status !== "uploading") return it;
            if (it.progress >= 90) return it;
            return { ...it, progress: Math.min(90, it.progress + 10) };
          }),
        );
      }, 220);

      uploadMutation.mutate(file, {
        onSuccess: (ack) => {
          clearQueueInterval(tempId);
          updateQueueItem(tempId, {
            status: "uploaded",
            progress: 100,
            ackId: ack.id,
            ackMessage: ack.message,
          });
          toast.success(`Uploaded "${ack.name}"`, { description: ack.message });
          qc.invalidateQueries({ queryKey: ["documents"] });
        },
        onError: (err: unknown) => {
          clearQueueInterval(tempId);
          const msg =
            err instanceof Error ? err.message : "Upload failed";
          updateQueueItem(tempId, { status: "failed", error: msg });
          toast.error(`Failed to upload "${file.name}"`, {
            description: msg,
          });
        },
      });
    }
  };

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (e.dataTransfer?.files?.length) handleFiles(e.dataTransfer.files);
  };

  const triggerBrowse = () => fileInputRef.current?.click();

  const handleProcess = (id: string, name: string) => {
    processMutation.mutate(id, {
      onSuccess: () => {
        toast.success(`Processing started for "${name}"`, {
          description: "OCR extraction is now queued.",
        });
      },
      onError: (err: unknown) => {
        const msg = err instanceof Error ? err.message : "Process failed";
        toast.error(`Failed to process "${name}"`, { description: msg });
      },
    });
  };

  const handleDelete = (d: DocumentMetadata) => {
    deleteMutation.mutate(d.id, {
      onSuccess: () => toast.success(`Deleted "${d.name}"`),
      onError: (err: unknown) => {
        const msg = err instanceof Error ? err.message : "Delete failed";
        toast.error(`Failed to delete "${d.name}"`, { description: msg });
      },
    });
  };

  /* ---- Corporate DB search / filter ---- */
  const [search, setSearch] = React.useState("");
  const [typeFilter, setTypeFilter] = React.useState<string>("all");
  const [selectedCorporate, setSelectedCorporate] = React.useState<Record<string, boolean>>({});

  const filteredCorporate = corporateDocs.filter((d) => {
    const q = search.trim().toLowerCase();
    const matchesSearch = !q || d.name.toLowerCase().includes(q);
    const matchesType = typeFilter === "all" || d.type === typeFilter;
    return matchesSearch && matchesType;
  });

  const selectedCount = Object.values(selectedCorporate).filter(Boolean).length;

  const toggleSelect = (id: string, checked: boolean) => {
    setSelectedCorporate((prev) => ({ ...prev, [id]: checked }));
  };

  const clearSelection = () => setSelectedCorporate({});

  /* --------------------------- Render --------------------------- */

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6">
      <PageHeader
        eyebrow="Step 1"
        title="Documents"
        description="Upload new files or select from the corporate document database to feed the extraction pipeline."
        icon={<FileStack className="size-5" />}
        actions={
          <Button onClick={triggerBrowse}>
            <Upload className="size-4" />
            Upload Documents
          </Button>
        }
      />

      {/* Hidden file input shared by the header button + dropzone */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        aria-hidden="true"
        tabIndex={-1}
        onChange={(e) => {
          if (e.target.files?.length) handleFiles(e.target.files);
          e.target.value = "";
        }}
      />

      {/* Stat strip */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {docsQ.isLoading ? (
          Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-xl" />
          ))
        ) : (
          <>
            <StatCard
              label="Total documents"
              value={docs.length}
              hint="Across all sources"
              icon={<FileStack className="size-5" />}
              tone="primary"
            />
            <StatCard
              label="Uploaded"
              value={uploadedDocs.length}
              hint="User-supplied files"
              icon={<Upload className="size-5" />}
              tone="default"
            />
            <StatCard
              label="Corporate DB"
              value={corporateDocs.length}
              hint={`${selectedCount} selected for use`}
              icon={<Database className="size-5" />}
              tone="success"
            />
          </>
        )}
      </div>

      {/* Main grid: upload zone + queue | corporate DB */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* ---- LEFT (main) ---- */}
        <div className="space-y-5 lg:col-span-2">
          <motion.div
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={triggerBrowse}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                triggerBrowse();
              }
            }}
            className={`group relative flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
              isDragging
                ? "border-primary bg-primary/5"
                : "border-border hover:border-primary/40 hover:bg-muted/40"
            }`}
          >
            <div className="flex size-14 items-center justify-center rounded-full bg-primary/10 text-primary ring-1 ring-inset ring-primary/15">
              <UploadCloud className="size-6" />
            </div>
            <p className="mt-4 text-sm font-semibold">
              Drag &amp; drop files here or click to browse
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              PDF, DOCX, PNG, JPG — multiple files supported
            </p>
            <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
              <Button
                size="sm"
                variant="default"
                onClick={(e) => {
                  e.stopPropagation();
                  triggerBrowse();
                }}
              >
                <Upload className="size-4" />
                Browse files
              </Button>
              <span className="text-xs text-muted-foreground">or drop them above</span>
            </div>
          </motion.div>

          {/* Upload queue */}
          <SectionCard
            title="Upload queue"
            description={`${queue.length} file${queue.length === 1 ? "" : "s"} in queue`}
            actions={
              queue.length > 0 ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    queue.forEach((q) => clearQueueInterval(q.tempId));
                    setQueue([]);
                  }}
                >
                  <Trash2 className="size-3.5" />
                  Clear
                </Button>
              ) : null
            }
          >
            {queue.length === 0 ? (
              <EmptyState
                icon={<UploadCloud className="size-5" />}
                title="No uploads queued"
                description="Files you select or drop will appear here with live upload progress."
              />
            ) : (
              <ScrollArea className="max-h-80">
                <ul className="space-y-2 pr-2">
                  <AnimatePresence initial={false}>
                    {queue.map((item) => (
                      <motion.li
                        key={item.tempId}
                        layout
                        initial={{ opacity: 0, y: -6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.2 }}
                        className="rounded-lg border border-border/70 bg-card p-3"
                      >
                        <div className="flex items-start gap-3">
                          <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                            <FileText className="size-4" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center justify-between gap-2">
                              <p className="truncate text-sm font-medium">
                                {item.file.name}
                              </p>
                              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                                {formatBytes(item.file.size)}
                              </span>
                            </div>

                            <div className="mt-2 flex items-center gap-2">
                              <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                                <motion.div
                                  className="absolute inset-y-0 left-0 rounded-full bg-primary"
                                  initial={{ width: 0 }}
                                  animate={{ width: `${item.progress}%` }}
                                  transition={{ duration: 0.3, ease: "easeOut" }}
                                />
                              </div>
                              <span className="w-9 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                                {item.progress}%
                              </span>
                            </div>

                            <div className="mt-2 flex items-center justify-between gap-2">
                              <QueueStatusPill status={item.status} />
                              <div className="flex items-center gap-1.5">
                                {item.status === "uploaded" && item.ackId ? (
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={() =>
                                      handleProcess(item.ackId!, item.file.name)
                                    }
                                    disabled={processMutation.isPending}
                                  >
                                    {processMutation.isPending ? (
                                      <Loader2 className="size-3.5 animate-spin" />
                                    ) : (
                                      <Play className="size-3.5" />
                                    )}
                                    Process
                                  </Button>
                                ) : null}
                                <Button
                                  size="icon"
                                  variant="ghost"
                                  className="size-7"
                                  onClick={() => removeQueueItem(item.tempId)}
                                  aria-label="Remove from queue"
                                >
                                  <Trash2 className="size-3.5" />
                                </Button>
                              </div>
                            </div>

                            {item.error ? (
                              <p className="mt-1.5 text-xs text-rose-600 dark:text-rose-400">
                                {item.error}
                              </p>
                            ) : null}
                          </div>
                        </div>
                      </motion.li>
                    ))}
                  </AnimatePresence>
                </ul>
              </ScrollArea>
            )}
          </SectionCard>
        </div>

        {/* ---- RIGHT ---- */}
        <SectionCard
          title="Corporate document database"
          description="Pick pre-indexed files to include in your extraction workflow"
          actions={
            <Badge tone={selectedCount > 0 ? "teal" : "slate"}>
              {selectedCount} selected
            </Badge>
          }
        >
          <div className="space-y-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search documents…"
                className="pl-8"
                aria-label="Search corporate documents"
              />
            </div>

            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="w-full">
                <Filter className="size-3.5 text-muted-foreground" />
                <SelectValue placeholder="Filter by type" />
              </SelectTrigger>
              <SelectContent>
                {TYPE_FILTERS.map((t) => (
                  <SelectItem key={t.value} value={t.value}>
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <div className="max-h-[420px] space-y-1.5 overflow-y-auto scrollbar-thin pr-1">
              {docsQ.isLoading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-14 w-full" />
                ))
              ) : filteredCorporate.length === 0 ? (
                <EmptyState
                  icon={<Database className="size-5" />}
                  title="No documents found"
                  description="Try a different search query or filter."
                />
              ) : (
                filteredCorporate.map((d, i) => {
                  const checked = !!selectedCorporate[d.id];
                  return (
                    <motion.div
                      key={d.id}
                      custom={i}
                      variants={fade}
                      initial="hidden"
                      animate="show"
                      onClick={() => toggleSelect(d.id, !checked)}
                      className={`flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-colors ${
                        checked
                          ? "border-primary/40 bg-primary/5"
                          : "border-border/60 hover:bg-muted/40"
                      }`}
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={(c) => toggleSelect(d.id, c === true)}
                        onClick={(e) => e.stopPropagation()}
                        className="mt-0.5"
                        aria-label={`Select ${d.name}`}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-sm font-medium">{d.name}</p>
                          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                            {d.page_count}p
                          </span>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                          <Badge tone="teal">{d.collection ?? "General"}</Badge>
                          <Badge tone="slate">{docTypeLabel(d.type)}</Badge>
                        </div>
                      </div>
                    </motion.div>
                  );
                })
              )}
            </div>

            {selectedCount > 0 ? (
              <div className="flex items-center justify-between gap-2 border-t border-border/60 pt-3">
                <span className="text-xs text-muted-foreground">
                  {selectedCount} document{selectedCount === 1 ? "" : "s"} ready
                </span>
                <div className="flex items-center gap-1.5">
                  <Button size="sm" variant="ghost" onClick={clearSelection}>
                    Clear
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      toast.success("Selection confirmed", {
                        description: `${selectedCount} document${
                          selectedCount === 1 ? "" : "s"
                        } added to the workflow.`,
                      })
                    }
                  >
                    <Plus className="size-3.5" />
                    Add to workflow
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        </SectionCard>
      </div>

      {/* All documents table */}
      <SectionCard
        title="All documents"
        description="Every file in your workspace — uploaded and corporate"
        actions={
          <span className="text-xs text-muted-foreground">
            {docs.length} total · {uploadedDocs.length} uploaded ·{" "}
            {corporateDocs.length} corporate
          </span>
        }
        noBodyPadding
      >
        {docsQ.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : docs.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={<FileStack className="size-5" />}
              title="No documents yet"
              description="Upload files or pick from the corporate database to get started."
              action={
                <Button size="sm" onClick={triggerBrowse}>
                  <Upload className="size-4" />
                  Upload documents
                </Button>
              }
            />
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead>Name</TableHead>
                <TableHead className="hidden sm:table-cell">Source</TableHead>
                <TableHead className="hidden md:table-cell">Type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="hidden lg:table-cell">Confidence</TableHead>
                <TableHead className="hidden lg:table-cell">Added</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {docs.map((d, i) => (
                <motion.tr
                  key={d.id}
                  custom={i}
                  variants={fade}
                  initial="hidden"
                  animate="show"
                  className="border-b transition-colors hover:bg-muted/50"
                >
                  <TableCell className="max-w-[260px] font-medium">
                    <div className="flex items-center gap-2.5">
                      <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                        <FileText className="size-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{d.name}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {d.page_count} pages · {formatBytes(d.size_bytes)}
                        </p>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="hidden sm:table-cell">
                    <SourceBadge source={d.source} />
                  </TableCell>
                  <TableCell className="hidden md:table-cell text-xs text-muted-foreground">
                    {docTypeLabel(d.type)}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={d.status} />
                  </TableCell>
                  <TableCell className="hidden lg:table-cell">
                    {d.confidence !== null && d.confidence > 0 ? (
                      <span className="text-xs tabular-nums">
                        {pct(d.confidence, 0)}
                      </span>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="hidden lg:table-cell text-xs text-muted-foreground">
                    {formatRelative(d.uploaded_at)}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => review(d.id)}
                      >
                        Review
                        <ArrowRight className="size-3.5" />
                      </Button>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            size="icon"
                            variant="ghost"
                            className="size-7"
                            aria-label="More actions"
                          >
                            <MoreHorizontal className="size-3.5" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => review(d.id)}>
                            <FileText className="size-3.5" />
                            View / Review
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => handleDelete(d)}
                            disabled={deleteMutation.isPending}
                          >
                            <Trash2 className="size-3.5" />
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </TableCell>
                </motion.tr>
              ))}
            </TableBody>
          </Table>
        )}
      </SectionCard>
    </div>
  );
}
