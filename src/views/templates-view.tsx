"use client";

import * as React from "react";
import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import {
  FileCode2,
  Plus,
  Pencil,
  Trash2,
  Play,
  Gauge,
  ArrowLeft,
  Save,
  X,
  GripVertical,
  Settings2,
  Wand2,
  Eye,
  Layers,
  MoreHorizontal,
  Sparkles,
  Loader2,
} from "lucide-react";

import { PageHeader } from "@/components/app/page-header";
import { SectionCard, EmptyState } from "@/components/app/section";
import { ConfidenceBadge, Badge } from "@/components/app/badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { templatesApi, ocrApi } from "@/lib/api";
import { useNav } from "@/lib/store";
import type {
  ExtractionTemplate,
  TemplateFieldDefinition,
  TemplateCreate,
  FieldType,
  DocumentType,
} from "@/lib/types";

/* ----------------------------- helpers ----------------------------- */

const uid = () => `f-${Math.random().toString(36).slice(2, 9)}`;
const slugify = (label: string) =>
  label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");

const FIELD_TYPES: FieldType[] = [
  "text", "number", "date", "currency", "email", "phone",
  "select", "multiselect", "boolean", "table", "regex",
];
const DOC_TYPES: DocumentType[] = [
  "invoice", "receipt", "contract", "report", "form", "id", "other",
];
const OCR_METHODS = ["advanced-ocr-standard", "basic-ocr", "cloud-vlm"];
const CHUNKING_STRATEGIES = ["page-by-page", "fixed-size", "semantic", "sliding-window"];

const FIELD_TYPE_LABEL: Record<FieldType, string> = {
  text: "Text",
  number: "Number",
  date: "Date",
  currency: "Currency",
  email: "Email",
  phone: "Phone",
  select: "Select",
  multiselect: "Multi-select",
  boolean: "Boolean",
  table: "Table",
  regex: "Regex",
};

const DOC_TYPE_LABEL: Record<DocumentType, string> = {
  invoice: "Invoice",
  receipt: "Receipt",
  contract: "Contract",
  report: "Report",
  form: "Form",
  id: "ID / Identity",
  other: "Other",
};

function makeEmptyField(): TemplateFieldDefinition {
  return {
    id: uid(),
    label: "",
    key: "",
    type: "text",
    example_value: "",
    validation_rule: null,
    required: false,
    notes: null,
    extraction_hint: null,
    default_value: null,
  };
}

function confidenceLevel(rate: number | null): "high" | "medium" | "low" | null {
  if (rate === null) return null;
  if (rate >= 0.9) return "high";
  if (rate >= 0.75) return "medium";
  return "low";
}

type EditorState = {
  mode: "create" | "edit";
  templateId?: string;
  template: Partial<TemplateCreate>;
  fields: TemplateFieldDefinition[];
};

const fade = {
  hidden: { opacity: 0, y: 8 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.04, duration: 0.3, ease: "easeOut" as const },
  }),
};

/* --------------------------- main view --------------------------- */

export function TemplatesView() {
  const qc = useQueryClient();
  const applyTemplate = useNav((s) => s.applyTemplate);
  const benchmarkTemplate = useNav((s) => s.benchmarkTemplate);

  const templatesQ = useQuery({
    queryKey: ["templates"],
    queryFn: () => templatesApi.list(),
  });

  // Seed-on-open: if the user arrived from OCR review "Approve → create
  // template", open the editor in create mode immediately with the source
  // document pre-filled. Using a lazy initializer avoids calling setState
  // synchronously inside an effect.
  const [editor, setEditor] = useState<EditorState | null>(() => {
    const docId = useNav.getState().createTemplateFromDocId;
    if (!docId) return null;
    return {
      mode: "create",
      template: {
        name: "",
        description: "",
        document_type: "invoice",
        ocr_method: "advanced-ocr-standard",
        chunking_strategy: "page-by-page",
        max_pages: 10,
        loop_condition: "EOF",
        source_document_id: docId,
      },
      fields: [makeEmptyField()],
    };
  });
  const [deleteId, setDeleteId] = useState<string | null>(null);

  // Clear the consumed nav flag once on mount.
  useEffect(() => {
    if (useNav.getState().createTemplateFromDocId) {
      useNav.setState({ createTemplateFromDocId: null });
    }
  }, []);

  const removeM = useMutation({
    mutationFn: (id: string) => templatesApi.remove(id),
    onSuccess: () => {
      toast.success("Template deleted");
      qc.invalidateQueries({ queryKey: ["templates"] });
      setDeleteId(null);
    },
    onError: (e) =>
      toast.error("Failed to delete template", { description: String(e) }),
  });

  const openCreate = () => {
    setEditor({
      mode: "create",
      template: {
        name: "",
        description: "",
        document_type: "invoice",
        ocr_method: "advanced-ocr-standard",
        chunking_strategy: "page-by-page",
        max_pages: 10,
        loop_condition: "EOF",
        source_document_id: null,
      },
      fields: [makeEmptyField()],
    });
  };

  const openEdit = (t: ExtractionTemplate) => {
    setEditor({
      mode: "edit",
      templateId: t.id,
      template: {
        name: t.name,
        description: t.description,
        document_type: t.document_type,
        ocr_method: t.ocr_method,
        chunking_strategy: t.chunking_strategy,
        max_pages: t.max_pages,
        loop_condition: t.loop_condition,
        source_document_id: t.source_document_id,
      },
      fields: t.fields.map((f) => ({ ...f })),
    });
  };

  if (editor) {
    return (
      <TemplateEditor
        initial={editor}
        onCancel={() => setEditor(null)}
        onSaved={() => setEditor(null)}
      />
    );
  }

  const templates = templatesQ.data ?? [];
  const deletingTemplate = templates.find((t) => t.id === deleteId) ?? null;

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6">
      <PageHeader
        eyebrow="Library"
        title="Extraction Templates"
        description="Define reusable field schemas, validation rules, and processing settings. Apply them to documents or benchmark their accuracy."
        icon={<FileCode2 className="size-5" />}
        actions={
          <Button onClick={openCreate}>
            <Plus className="size-4" />
            Create Template
          </Button>
        }
      />

      {templatesQ.isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-52 w-full rounded-xl" />
          ))}
        </div>
      ) : templates.length === 0 ? (
        <EmptyState
          icon={<FileCode2 className="size-5" />}
          title="No templates yet"
          description="Create your first extraction template to define fields, validation rules, and processing settings that can be reused across documents."
          action={
            <Button onClick={openCreate}>
              <Plus className="size-4" />
              Create your first template
            </Button>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {templates.map((t, i) => {
            const lvl = confidenceLevel(t.success_rate);
            return (
              <motion.div
                key={t.id}
                custom={i}
                variants={fade}
                initial="hidden"
                animate="show"
              >
                <Card className="group flex h-full flex-col gap-0 p-4 transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary ring-1 ring-inset ring-primary/15">
                      <FileCode2 className="size-4.5" />
                    </div>
                    {lvl ? (
                      <ConfidenceBadge level={lvl} />
                    ) : (
                      <Badge tone="slate">Unrated</Badge>
                    )}
                  </div>

                  <div className="mt-3 flex-1">
                    <p className="text-sm font-semibold leading-tight">
                      {t.name}
                    </p>
                    <p className="mt-1 line-clamp-2 min-h-[2rem] text-xs text-muted-foreground">
                      {t.description || "No description provided."}
                    </p>
                  </div>

                  <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-border/60 pt-3 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <Layers className="size-3.5" />
                      {t.fields.length} fields
                    </span>
                    <span aria-hidden>·</span>
                    <span className="flex items-center gap-1">
                      <Play className="size-3.5" />
                      {t.usage_count} runs
                    </span>
                    <span aria-hidden>·</span>
                    <span className="font-mono">v{t.version}</span>
                  </div>

                  <div className="mt-3 flex items-center gap-1.5">
                    <Button
                      size="sm"
                      variant="outline"
                      className="flex-1"
                      onClick={() => openEdit(t)}
                    >
                      <Pencil className="size-3.5" />
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="flex-1"
                      onClick={() => applyTemplate(t.id)}
                    >
                      <Play className="size-3.5" />
                      Apply
                    </Button>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          size="icon"
                          variant="outline"
                          className="size-8"
                          onClick={() => benchmarkTemplate(t.id)}
                        >
                          <Gauge className="size-3.5" />
                          <span className="sr-only">Benchmark</span>
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>Benchmark template</TooltipContent>
                    </Tooltip>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button size="icon" variant="outline" className="size-8">
                          <MoreHorizontal className="size-3.5" />
                          <span className="sr-only">More actions</span>
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-44">
                        <DropdownMenuItem
                          onSelect={(e) => {
                            e.preventDefault();
                            setDeleteId(t.id);
                          }}
                          variant="destructive"
                        >
                          <Trash2 className="size-3.5" />
                          Delete template
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </Card>
              </motion.div>
            );
          })}
        </div>
      )}

      {/* Delete confirmation */}
      <AlertDialog
        open={deleteId !== null}
        onOpenChange={(o) => !o && setDeleteId(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete template?</AlertDialogTitle>
            <AlertDialogDescription>
              {deletingTemplate
                ? `“${deletingTemplate.name}” will be permanently removed. This action cannot be undone.`
                : "This template will be permanently removed."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={removeM.isPending}
              onClick={(e) => {
                e.preventDefault();
                if (deleteId) removeM.mutate(deleteId);
              }}
            >
              {removeM.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Trash2 className="size-4" />
              )}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/* --------------------------- editor --------------------------- */

function TemplateEditor({
  initial,
  onCancel,
  onSaved,
}: {
  initial: EditorState;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const qc = useQueryClient();
  const [template, setTemplate] = useState<Partial<TemplateCreate>>(initial.template);
  const [fields, setFields] = useState<TemplateFieldDefinition[]>(initial.fields);
  // Start in "seeding" state when a source document is present so the loading
  // banner shows immediately; the flag is cleared asynchronously once the OCR
  // fields arrive (or fail).
  const [seeding, setSeeding] = useState(
    () => !!initial.template.source_document_id,
  );
  const seededRef = useRef(false);

  // Seed-on-open: pre-fill fields from the source document's OCR fields.
  // All setState calls here happen inside async callbacks (not synchronously
  // in the effect body) to avoid cascading renders.
  useEffect(() => {
    const docId = template.source_document_id;
    if (!docId || seededRef.current) return;
    seededRef.current = true;
    ocrApi
      .fields(docId)
      .then((ocrFields) => {
        if (ocrFields && ocrFields.length > 0) {
          const mapped: TemplateFieldDefinition[] = ocrFields
            .slice(0, 25)
            .map((f) => ({
              id: uid(),
              label: f.label,
              key: f.key || slugify(f.label),
              type: f.type,
              example_value:
                typeof f.value === "string"
                  ? f.value
                  : f.value == null
                    ? ""
                    : JSON.stringify(f.value),
              validation_rule: null,
              required: f.required,
              notes: f.notes,
              extraction_hint: null,
              default_value: null,
            }));
          setFields(mapped);
          toast.success(
            `Seeded ${mapped.length} field${mapped.length === 1 ? "" : "s"} from document`,
          );
        }
      })
      .catch((e) => {
        toast.error("Could not load document fields", { description: String(e) });
      })
      .finally(() => setSeeding(false));
  }, []);

  const saveM = useMutation({
    mutationFn: async () => {
      const payload: TemplateCreate = {
        name: template.name ?? "",
        description: template.description ?? null,
        document_type: (template.document_type ?? "other") as DocumentType,
        fields,
        ocr_method: template.ocr_method ?? "advanced-ocr-standard",
        chunking_strategy: template.chunking_strategy ?? "page-by-page",
        max_pages: template.max_pages ?? 10,
        loop_condition: template.loop_condition ?? null,
        source_document_id: template.source_document_id ?? null,
      };
      if (initial.mode === "edit" && initial.templateId) {
        return templatesApi.update(initial.templateId, payload);
      }
      return templatesApi.create(payload);
    },
    onSuccess: () => {
      toast.success(
        initial.mode === "edit" ? "Template updated" : "Template created",
      );
      qc.invalidateQueries({ queryKey: ["templates"] });
      onSaved();
    },
    onError: (e) =>
      toast.error("Failed to save template", { description: String(e) }),
  });

  const handleSave = () => {
    if (!template.name?.trim()) {
      toast.error("Template name is required");
      return;
    }
    if (fields.length === 0) {
      toast.error("Add at least one field");
      return;
    }
    saveM.mutate();
  };

  /* ---- field ops ---- */
  const addField = () => setFields((fs) => [...fs, makeEmptyField()]);
  const removeField = (id: string) =>
    setFields((fs) => fs.filter((f) => f.id !== id));
  const updateField = (id: string, patch: Partial<TemplateFieldDefinition>) =>
    setFields((fs) => fs.map((f) => (f.id === id ? { ...f, ...patch } : f)));

  const onLabelChange = (id: string, label: string) => {
    const cur = fields.find((f) => f.id === id);
    // Auto-suggest key from label only if key is empty or was previously auto-derived.
    const shouldAutoKey = !cur || !cur.key || cur.key === slugify(cur.label);
    updateField(id, {
      label,
      key: shouldAutoKey ? slugify(label) : cur!.key,
    });
  };

  const setT = <K extends keyof TemplateCreate>(
    key: K,
    value: TemplateCreate[K],
  ) => setTemplate((t) => ({ ...t, [key]: value }));

  const docType = (template.document_type ?? "other") as DocumentType;
  const ocrMethod = template.ocr_method ?? "advanced-ocr-standard";
  const chunking = template.chunking_strategy ?? "page-by-page";
  const maxPages = template.max_pages ?? 10;
  const loopCond = template.loop_condition ?? "EOF";

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6">
      {/* Header bar */}
      <div className="flex flex-col gap-3 rounded-xl border border-border/70 bg-card p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Button variant="outline" size="sm" onClick={onCancel}>
            <ArrowLeft className="size-4" />
            Back to templates
          </Button>
          <Separator orientation="vertical" className="hidden h-6 sm:block" />
          <div className="flex items-center gap-2">
            <Wand2 className="size-4 text-primary" />
            <Input
              className="h-9 sm:w-72"
              placeholder="Template name…"
              value={template.name ?? ""}
              onChange={(e) => setT("name", e.target.value)}
              aria-label="Template name"
            />
          </div>
          <Badge tone="teal" className="hidden sm:inline-flex">
            {initial.mode === "edit" ? "Editing" : "New template"}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={onCancel}>
            <X className="size-4" />
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={saveM.isPending}>
            {saveM.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            Save template
          </Button>
        </div>
      </div>

      {seeding && (
        <div className="flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-4 py-2.5 text-sm text-primary">
          <Loader2 className="size-4 animate-spin" />
          Loading fields from source document…
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        {/* Left column: form (60%) */}
        <div className="space-y-6 lg:col-span-3">
          {/* General settings */}
          <SectionCard
            title="General settings"
            description="Identity and scope of this template"
          >
            <div className="space-y-4">
              <FieldRow label="Template name" required>
                <Input
                  placeholder="e.g. Standard Invoice Extraction"
                  value={template.name ?? ""}
                  onChange={(e) => setT("name", e.target.value)}
                />
              </FieldRow>
              <FieldRow label="Description">
                <Textarea
                  rows={3}
                  placeholder="What is this template for? Which document family does it target?"
                  value={template.description ?? ""}
                  onChange={(e) => setT("description", e.target.value)}
                />
              </FieldRow>
              <FieldRow label="Document type" required>
                <Select
                  value={docType}
                  onValueChange={(v) => setT("document_type", v as DocumentType)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {DOC_TYPES.map((dt) => (
                      <SelectItem key={dt} value={dt}>
                        {DOC_TYPE_LABEL[dt]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FieldRow>
            </div>
          </SectionCard>

          {/* Advanced adjustments */}
          <SectionCard
            title="Advanced adjustments"
            description="OCR pipeline, chunking & looping behavior"
          >
            <div className="space-y-5">
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Settings2 className="size-3.5 text-muted-foreground" />
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Document handling
                  </p>
                </div>
                <FieldRow label="OCR method">
                  <Select
                    value={ocrMethod}
                    onValueChange={(v) => setT("ocr_method", v)}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {OCR_METHODS.map((m) => (
                        <SelectItem key={m} value={m}>
                          {m}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FieldRow>
              </div>

              <Separator />

              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Layers className="size-3.5 text-muted-foreground" />
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Document chunking
                  </p>
                </div>
                <FieldRow label="Chunking strategy">
                  <Select
                    value={chunking}
                    onValueChange={(v) => setT("chunking_strategy", v)}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CHUNKING_STRATEGIES.map((c) => (
                        <SelectItem key={c} value={c}>
                          {c}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FieldRow>
              </div>

              <Separator />

              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Gauge className="size-3.5 text-muted-foreground" />
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Looping mechanism
                  </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <FieldRow label="Maximum pages">
                    <Input
                      type="number"
                      min={1}
                      max={1000}
                      value={maxPages}
                      onChange={(e) =>
                        setT("max_pages", Number(e.target.value) || 0)
                      }
                    />
                  </FieldRow>
                  <FieldRow label="Loop condition" hint="e.g., EOF">
                    <Input
                      placeholder="EOF"
                      value={loopCond ?? ""}
                      onChange={(e) => setT("loop_condition", e.target.value)}
                    />
                  </FieldRow>
                </div>
              </div>
            </div>
          </SectionCard>

          {/* Field definitions editor */}
          <SectionCard
            title="Field definitions"
            description={`${fields.length} field${fields.length === 1 ? "" : "s"} · drag handle for visual order`}
            actions={
              <Button size="sm" variant="outline" onClick={addField}>
                <Plus className="size-3.5" />
                Add field
              </Button>
            }
          >
            {fields.length === 0 ? (
              <EmptyState
                icon={<Layers className="size-5" />}
                title="No fields defined"
                description="Add at least one field so this template can extract structured data."
                action={
                  <Button size="sm" onClick={addField}>
                    <Plus className="size-4" />
                    Add first field
                  </Button>
                }
              />
            ) : (
              <div className="space-y-3">
                <AnimatePresence initial={false}>
                  {fields.map((f, idx) => (
                    <motion.div
                      key={f.id}
                      layout
                      initial={{ opacity: 0, scale: 0.97, y: -6 }}
                      animate={{ opacity: 1, scale: 1, y: 0 }}
                      exit={{ opacity: 0, scale: 0.97, y: -6 }}
                      transition={{ duration: 0.18, ease: "easeOut" }}
                    >
                      <Card className="gap-0 p-3">
                        {/* Row 1: name, key, type, delete */}
                        <div className="grid gap-3 sm:grid-cols-[auto_1fr_1fr_150px_auto] sm:items-end">
                          <div className="hidden items-center justify-center pt-6 sm:flex">
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <span className="cursor-grab text-muted-foreground">
                                  <GripVertical className="size-4" />
                                </span>
                              </TooltipTrigger>
                              <TooltipContent>
                                Field #{idx + 1}
                              </TooltipContent>
                            </Tooltip>
                          </div>
                          <FieldRow label="Field name">
                            <Input
                              placeholder="e.g. Invoice number"
                              value={f.label}
                              onChange={(e) => onLabelChange(f.id, e.target.value)}
                            />
                          </FieldRow>
                          <FieldRow label="Key">
                            <Input
                              placeholder="auto"
                              className="font-mono text-xs"
                              value={f.key}
                              onChange={(e) =>
                                updateField(f.id, { key: e.target.value })
                              }
                            />
                          </FieldRow>
                          <FieldRow label="Type">
                            <Select
                              value={f.type}
                              onValueChange={(v) =>
                                updateField(f.id, { type: v as FieldType })
                              }
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {FIELD_TYPES.map((ft) => (
                                  <SelectItem key={ft} value={ft}>
                                    {FIELD_TYPE_LABEL[ft]}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </FieldRow>
                          <div className="flex items-center justify-end pt-6">
                            <Button
                              size="icon"
                              variant="ghost"
                              className="size-8 text-muted-foreground hover:text-destructive"
                              onClick={() => removeField(f.id)}
                              aria-label="Delete field"
                            >
                              <Trash2 className="size-4" />
                            </Button>
                          </div>
                        </div>

                        {/* Row 2: example value + validation rule */}
                        <div className="mt-3 grid gap-3 sm:grid-cols-2">
                          <FieldRow label="Example value">
                            <Input
                              placeholder="e.g. INV-000123"
                              value={f.example_value ?? ""}
                              onChange={(e) =>
                                updateField(f.id, { example_value: e.target.value })
                              }
                            />
                          </FieldRow>
                          <FieldRow
                            label="Validation rule (regex)"
                            hint={f.validation_rule ? "regex pattern" : "optional"}
                          >
                            <Input
                              placeholder="^RC-\\d{4}-\\d{4}$"
                              className="font-mono text-xs"
                              value={f.validation_rule ?? ""}
                              onChange={(e) =>
                                updateField(f.id, { validation_rule: e.target.value })
                              }
                            />
                          </FieldRow>
                        </div>

                        {/* Row 3: notes + required */}
                        <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-end">
                          <FieldRow label="Notes" className="flex-1">
                            <Input
                              placeholder="Optional hints for extraction engine"
                              value={f.notes ?? ""}
                              onChange={(e) =>
                                updateField(f.id, { notes: e.target.value })
                              }
                            />
                          </FieldRow>
                          <div className="flex items-center gap-2 pb-1.5">
                            <Switch
                              id={`req-${f.id}`}
                              checked={f.required}
                              onCheckedChange={(c) =>
                                updateField(f.id, { required: c })
                              }
                            />
                            <Label htmlFor={`req-${f.id}`} className="text-sm">
                              Required
                            </Label>
                          </div>
                        </div>
                      </Card>
                    </motion.div>
                  ))}
                </AnimatePresence>

                <Button
                  variant="outline"
                  className="w-full border-dashed"
                  onClick={addField}
                >
                  <Plus className="size-4" />
                  Add another field
                </Button>
              </div>
            )}
          </SectionCard>
        </div>

        {/* Right column: live preview (40%) */}
        <div className="lg:col-span-2">
          <div className="sticky top-4 space-y-4">
            <SectionCard
              title="Live preview"
              description="Read-only visualization of the template"
              actions={
                <Badge tone="emerald" className="gap-1">
                  <Eye className="size-3" />
                  Preview
                </Badge>
              }
            >
              <div className="space-y-4">
                <div>
                  <p className="text-xs text-muted-foreground">Template name</p>
                  <p className="text-base font-semibold leading-tight">
                    {template.name?.trim() || (
                      <span className="italic text-muted-foreground">Untitled template</span>
                    )}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <Badge tone="teal">{DOC_TYPE_LABEL[docType]}</Badge>
                    {template.source_document_id ? (
                      <Badge tone="violet" className="gap-1">
                        <Sparkles className="size-3" />
                        Seeded from doc
                      </Badge>
                    ) : null}
                  </div>
                  {template.description?.trim() ? (
                    <p className="mt-2 line-clamp-3 text-xs text-muted-foreground">
                      {template.description}
                    </p>
                  ) : null}
                </div>

                <Separator />

                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Fields ({fields.length})
                  </p>
                  {fields.length === 0 ? (
                    <p className="rounded-md border border-dashed border-border/60 bg-muted/20 px-3 py-4 text-center text-xs text-muted-foreground">
                      No fields yet — add one to see the preview.
                    </p>
                  ) : (
                    <div className="max-h-80 space-y-2.5 overflow-y-auto pr-1">
                      {fields.map((f) => (
                        <div key={f.id} className="space-y-1">
                          <Label className="flex items-center gap-1 text-xs">
                            <span className="truncate">
                              {f.label?.trim() || (
                                <span className="italic text-muted-foreground">Untitled</span>
                              )}
                            </span>
                            {f.required ? (
                              <span className="text-destructive">*</span>
                            ) : null}
                            <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                              {f.key || "—"}
                            </span>
                          </Label>
                          <div className="flex h-8 items-center rounded-md border border-dashed border-border/60 bg-muted/20 px-2.5 text-xs">
                            {f.example_value?.trim() ? (
                              <span className="truncate text-foreground">
                                {f.example_value}
                              </span>
                            ) : (
                              <span className="italic text-muted-foreground/60">
                                e.g. value
                              </span>
                            )}
                            <span className="ml-auto rounded bg-background px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                              {FIELD_TYPE_LABEL[f.type]}
                            </span>
                          </div>
                          {f.validation_rule?.trim() ? (
                            <p className="truncate font-mono text-[10px] text-muted-foreground">
                              /{f.validation_rule}/
                            </p>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <Separator />

                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Advanced settings
                  </p>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                    <dt className="text-muted-foreground">OCR method</dt>
                    <dd className="truncate font-mono">{ocrMethod}</dd>
                    <dt className="text-muted-foreground">Chunking</dt>
                    <dd className="truncate font-mono">{chunking}</dd>
                    <dt className="text-muted-foreground">Max pages</dt>
                    <dd className="font-mono">{maxPages}</dd>
                    <dt className="text-muted-foreground">Loop condition</dt>
                    <dd className="font-mono">{loopCond || "—"}</dd>
                  </dl>
                </div>
              </div>
            </SectionCard>
          </div>
        </div>
      </div>
    </div>
  );
}

/* --------------------------- field row helper --------------------------- */

function FieldRow({
  label,
  required,
  hint,
  children,
  className,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`space-y-1.5 ${className ?? ""}`}>
      <Label className="text-xs text-muted-foreground">
        {label}
        {required ? <span className="text-destructive">*</span> : null}
      </Label>
      {children}
      {hint ? (
        <p className="text-[11px] text-muted-foreground/80">{hint}</p>
      ) : null}
    </div>
  );
}
