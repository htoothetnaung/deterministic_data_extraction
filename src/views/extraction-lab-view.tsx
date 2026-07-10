"use client";

import * as React from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  ArrowUp,
  Braces,
  CheckCircle2,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Code2,
  Copy,
  Database,
  Download,
  ExternalLink,
  FileJson,
  FileText,
  FlaskConical as _FlaskConical,
  HelpCircle,
  Info,
  Loader2,
  MapPin,
  Maximize2,
  MoreVertical,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCw,
  Save,
  Settings2,
  Table2,
  Trash2,
  Upload,
  Wand2,
  ZoomIn,
  ZoomOut,
  X,
  Type,
  Hash,
  ToggleLeft,
  List,
  Search,
  Calendar,
} from "lucide-react";

import { Badge } from "@/components/app/badges";
// PageHeader import retained for potential reuse
// import { PageHeader } from "@/components/app/page-header";
import { EmptyState, SectionCard } from "@/components/app/section";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { extractionLabApi, parserBenchmarksApi } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  ChunkingStrategy,
  ExtractionChunk,
  ExtractionEvidence,
  ExtractionFieldResult,
  ExtractionFieldType,
  ExtractionLabSchema,
  ExtractionLabSchemaTemplate,
  ExtractionRunResponse,
  ExtractionSchemaField,
  MultiDocumentMode,
  ParserInfo,
  ParserInputInfo,
  JobHistoryItem,
} from "@/lib/types";

const EMPTY_INPUTS: ParserInputInfo[] = [];
const EMPTY_PARSERS: ParserInfo[] = [];
const EMPTY_SCHEMA_TEMPLATES: ExtractionLabSchemaTemplate[] = [];
const EXTRACTION_PARSER_IDS = ["layout_pdfplumber", "mistral_ocr", "paddleocr_vl_vllm", "docling"];

const FIELD_TYPES: ExtractionFieldType[] = [
  "text",
  "number",
  "boolean",
  "object",
  "list",
];

const FIELD_TYPE_LABEL: Record<ExtractionFieldType, string> = {
  text: "STR",
  number: "NUM",
  date: "STR",
  currency: "NUM",
  email: "STR",
  phone: "STR",
  boolean: "Boolean",
  list: "Array",
  table: "Array",
  object: "OBJ",
};

type ExtractTier = "cost_effective" | "agentic";
type LatestParserRun = {
  runId: string;
  startedAt: string;
};

const uid = () => `x-${Math.random().toString(36).slice(2, 9)}`;
const slugify = (label: string) =>
  label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "field";

const EMPTY_SCHEMA: ExtractionLabSchema = {
  name: "ManualExtraction",
  description: "",
  fields: [],
};

function makeField(
  key: string,
  label: string,
  type: ExtractionFieldType = "text",
  required = false,
  description = "",
): ExtractionSchemaField {
  return {
    id: uid(),
    key,
    label,
    type,
    description,
    required,
    children: [],
  };
}

function cloneSchema(schema: ExtractionLabSchema): ExtractionLabSchema {
  return {
    name: schema.name,
    description: schema.description ?? null,
    fields: schema.fields.map(cloneField),
  };
}

function cloneField(field: ExtractionSchemaField): ExtractionSchemaField {
  return {
    ...field,
    id: field.id || uid(),
    type: normalizeBuilderFieldType(field.type),
    description: field.description ?? null,
    children: (field.children ?? []).map(cloneField),
  };
}

function updateFieldTree(
  fields: ExtractionSchemaField[],
  id: string,
  patch: Partial<ExtractionSchemaField>,
): ExtractionSchemaField[] {
  return fields.map((field) => {
    if (field.id === id) {
      const nextType = patch.type ?? field.type;
      return {
        ...field,
        ...patch,
        children: nextType === "object" || nextType === "list" ? patch.children ?? field.children ?? [] : [],
      };
    }
    return { ...field, children: updateFieldTree(field.children ?? [], id, patch) };
  });
}

function updateFieldLabelTree(fields: ExtractionSchemaField[], id: string, label: string): ExtractionSchemaField[] {
  return fields.map((field) => {
    if (field.id === id) {
      const shouldKey = !field.key || field.key === slugify(field.label);
      return { ...field, label, key: shouldKey ? slugify(label) : field.key };
    }
    return { ...field, children: updateFieldLabelTree(field.children ?? [], id, label) };
  });
}

function removeFieldFromTree(fields: ExtractionSchemaField[], id: string): ExtractionSchemaField[] {
  return fields
    .filter((field) => field.id !== id)
    .map((field) => ({ ...field, children: removeFieldFromTree(field.children ?? [], id) }));
}

function addChildFieldToTree(fields: ExtractionSchemaField[], parentId: string): ExtractionSchemaField[] {
  return fields.map((field) => {
    if (field.id === parentId) {
      return {
        ...field,
        children: [...(field.children ?? []), makeField("newProperty", "New Property")],
      };
    }
    return { ...field, children: addChildFieldToTree(field.children ?? [], parentId) };
  });
}

function mapFieldTree(
  fields: ExtractionSchemaField[],
  mapper: (field: ExtractionSchemaField) => ExtractionSchemaField,
): ExtractionSchemaField[] {
  return fields.map((field) => {
    const next = mapper(field);
    return {
      ...next,
      children: mapFieldTree(next.children ?? [], mapper),
    };
  });
}

export function ExtractionLabView() {
  const qc = useQueryClient();
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const inputsQ = useQuery({
    queryKey: ["extraction-lab-inputs"],
    queryFn: () => extractionLabApi.inputs(),
  });
  const parsersQ = useQuery({
    queryKey: ["extraction-lab-parsers"],
    queryFn: () => extractionLabApi.parsers(),
  });
  const parserRunsQ = useQuery({
    queryKey: ["parser-runs"],
    queryFn: () => parserBenchmarksApi.runs(),
  });
  const schemaTemplatesQ = useQuery({
    queryKey: ["extraction-lab-schemas"],
    queryFn: () => extractionLabApi.schemas(),
  });

  const inputs = inputsQ.data ?? EMPTY_INPUTS;
  const parsers = parsersQ.data ?? EMPTY_PARSERS;
  const schemaTemplates = schemaTemplatesQ.data ?? EMPTY_SCHEMA_TEMPLATES;
  const [selectedInputId, setSelectedInputId] = React.useState("");
  const [selectedInputIds, setSelectedInputIds] = React.useState<string[]>([]);
  const [multiDocumentMode, setMultiDocumentMode] = React.useState<MultiDocumentMode>("per_document");
  const [parserId, setParserId] = React.useState("auto");
  const [schema, setSchema] = React.useState<ExtractionLabSchema>(() => cloneSchema(EMPTY_SCHEMA));
  const [schemaJson, setSchemaJson] = React.useState(() => JSON.stringify(EMPTY_SCHEMA, null, 2));
  const [schemaJsonError, setSchemaJsonError] = React.useState<string | null>(null);
  const [naturalLanguageQuery, setNaturalLanguageQuery] = React.useState("");
  const [maxPages, setMaxPages] = React.useState(20);
  const [maxCandidates, setMaxCandidates] = React.useState(8);
  const [chunkingStrategy, setChunkingStrategy] = React.useState<ChunkingStrategy>("table_row");
  const [chunkSize, setChunkSize] = React.useState(500);
  const [chunkOverlap, setChunkOverlap] = React.useState(80);
  const [advancedOpen, setAdvancedOpen] = React.useState(false);
  
  // Model Settings
  const [modelTier, setModelTier] = React.useState("cost_effective");
  const [temperature, setTemperature] = React.useState(0.0);
  const [maxTokens, setMaxTokens] = React.useState(2048);

  // Retrieval Settings
  const [denseCandidateLimit, setDenseCandidateLimit] = React.useState(10);
  const [sparseCandidateLimit, setSparseCandidateLimit] = React.useState(10);
  const [rankFusionConstant, setRankFusionConstant] = React.useState(60);
  const [scalarChunkLimit, setScalarChunkLimit] = React.useState(5);
  const [narrativeChunkLimit, setNarrativeChunkLimit] = React.useState(15);
  const [maxChunkLimit, setMaxChunkLimit] = React.useState(30);
  const [retryChunkExpansion, setRetryChunkExpansion] = React.useState(5);

  // Queries Settings
  const [emptyResultsMaxRetry, setEmptyResultsMaxRetry] = React.useState(3);
  const [queryMinWords, setQueryMinWords] = React.useState(3);
  const [queryMaxWords, setQueryMaxWords] = React.useState(10);
  const [priorResultPreview, setPriorResultPreview] = React.useState(true);

  // Cost Settings
  const [maxPageCost, setMaxPageCost] = React.useState(10);
  const [maxJobCost, setMaxJobCost] = React.useState(100);
  const [webhooksOpen, setWebhooksOpen] = React.useState(false);
  const [webhookUrl, setWebhookUrl] = React.useState("");
  const [webhookEvents, setWebhookEvents] = React.useState("all");
  const [webhookPayloadFormat, setWebhookPayloadFormat] = React.useState("string");
  const [results, setResults] = React.useState<ExtractionRunResponse[]>([]);
  const [selectedResultId, setSelectedResultId] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [activeTab, setActiveTab] = React.useState("build");
  const [selectedFieldKey, setSelectedFieldKey] = React.useState<string | null>(null);
  const [hoveredFieldKey, setHoveredFieldKey] = React.useState<string | null>(null);
  const [activeResultTab, setActiveResultTab] = React.useState<"extract" | "json">("extract");
  const [confidenceThreshold, setConfidenceThreshold] = React.useState(90);
  const [resultZoom, setResultZoom] = React.useState(113);

  const selectedInput = React.useMemo(
    () => inputs.find((input) => input.id === selectedInputId),
    [inputs, selectedInputId],
  );
  const compatibleParsers = React.useMemo(
    () =>
      selectedInput
        ? parsers.filter((parser) => parser.supported_input_types.includes(selectedInput.input_type))
        : [],
    [parsers, selectedInput],
  );
  const matchingRuns = React.useMemo(
    () => (parserRunsQ.data ?? []).filter((run) => run.input.id === selectedInputId).slice(0, 20),
    [parserRunsQ.data, selectedInputId],
  );
  const matchingRunDetailsQ = useQueries({
    queries: matchingRuns.map((run) => ({
      queryKey: ["parser-run", run.run_id],
      queryFn: () => parserBenchmarksApi.getRun(run.run_id),
      enabled: Boolean(run.run_id),
    })),
  });
  const latestByParser = React.useMemo(() => {
    const map = new Map<string, LatestParserRun>();
    const details = matchingRunDetailsQ
      .map((query) => query.data)
      .filter(Boolean)
      .sort((a, b) => Date.parse(b!.started_at) - Date.parse(a!.started_at));
    for (const run of details) {
      for (const result of run!.results) {
        if (result.status === "ok" && EXTRACTION_PARSER_IDS.includes(result.library) && !map.has(result.library)) {
          map.set(result.library, { runId: run!.run_id, startedAt: run!.started_at });
        }
      }
    }
    return map;
  }, [matchingRunDetailsQ]);
  const parserInstalled = compatibleParsers.some((parser) => parser.installed);
  const visibleResults = React.useMemo(
    () =>
      results.filter((item) => {
        if (!selectedInputId) return true;
        if (item.input.id === selectedInputId) return true;
        if (selectedInputIds.length > 1 && item.input.id.startsWith("bundle:")) return true;
        return selectedInputIds.includes(item.input.id);
      }),
    [results, selectedInputId, selectedInputIds],
  );
  const result = React.useMemo(
    () => visibleResults.find((item) => item.run_id === selectedResultId) ?? visibleResults[0] ?? null,
    [selectedResultId, visibleResults],
  );
  const pageCount = Math.max(selectedInput?.page_count ?? result?.stats.pages ?? 1, 1);
  const boundedPage = Math.min(Math.max(page, 1), pageCount);
  const selectedInputs = React.useMemo(
    () => selectedInputIds.map((id) => inputs.find((input) => input.id === id)).filter(Boolean) as ParserInputInfo[],
    [inputs, selectedInputIds],
  );



  React.useEffect(() => {
    setSchemaJson(JSON.stringify(schema, null, 2));
  }, [schema]);

  React.useEffect(() => {
    setPage((current) => Math.min(Math.max(current, 1), pageCount));
  }, [pageCount]);

  React.useEffect(() => {
    setPage(1);
    if (parserId === "auto") return;
    const stillCompatible = compatibleParsers.some((parser) => parser.id === parserId);
    if (!stillCompatible) setParserId("auto");
  }, [compatibleParsers, parserId, selectedInputId]);

  React.useEffect(() => {
    if (!selectedInputId) return;
    extractionLabApi.getResults(selectedInputId)
      .then((persistedResults) => {
        setResults((current) => {
          const combined = [...current, ...persistedResults];
          const seen = new Set<string>();
          return combined.filter((item) => {
            if (seen.has(item.run_id)) return false;
            seen.add(item.run_id);
            return true;
          });
        });
        if (persistedResults.length > 0) {
          setSelectedResultId((prev) => {
            const hasPrev = persistedResults.some((r) => r.run_id === prev);
            return hasPrev ? prev : persistedResults[0].run_id;
          });
        }
      })
      .catch((err) => {
        console.error("Failed to load persisted extraction results:", err);
      });
  }, [selectedInputId]);

  const uploadM = useMutation({
    mutationFn: (files: File[]) => (files.length === 1 ? extractionLabApi.upload(files[0]).then((input) => [input]) : extractionLabApi.uploadMany(files)),
    onSuccess: (uploaded) => {
      const inputIds = uploaded.map((input) => input.id);
      setSelectedInputId(inputIds[0] ?? "");
      setSelectedInputIds(inputIds);
      qc.invalidateQueries({ queryKey: ["extraction-lab-inputs"] });
      toast.success(uploaded.length === 1 ? "File uploaded" : "Files uploaded", {
        description: uploaded.length === 1 ? uploaded[0]?.name : `${uploaded.length} documents ready for extraction.`,
      });
    },
    onError: (error) => toast.error("Upload failed", { description: String(error) }),
  });

  const runM = useMutation({
    mutationFn: async () => {
      const inputIds = selectedInputIds.length ? selectedInputIds : selectedInputId ? [selectedInputId] : [];
      const primaryInputId = selectedInputId || inputIds[0] || "";
      const basePayload = {
        input_id: primaryInputId,
        output_schema: schema,
        natural_language_query: naturalLanguageQuery.trim() || null,
        parser_id: parserId,
        chunking_strategy: chunkingStrategy,
        chunk_size: chunkSize,
        chunk_overlap: chunkOverlap,
        max_pages: maxPages,
        max_candidates_per_field: maxCandidates,
        preview_chars: 8000,
        extraction_tier: "agentic",
        settings: {
          model: {
            model_tier: modelTier,
            temperature: Number(temperature),
            max_tokens: Number(maxTokens),
          },
          retrieval: {
            dense_candidate_limit: Number(denseCandidateLimit),
            sparse_candidate_limit: Number(sparseCandidateLimit),
            rank_fusion_constant: Number(rankFusionConstant),
            scalar_chunk_limit: Number(scalarChunkLimit),
            narrative_chunk_limit: Number(narrativeChunkLimit),
            max_chunk_limit: Number(maxChunkLimit),
            retry_chunk_expansion: Number(retryChunkExpansion),
          },
          queries: {
            empty_results_max_retry: Number(emptyResultsMaxRetry),
            query_min_words: Number(queryMinWords),
            query_max_words: Number(queryMaxWords),
            prior_result_preview: Boolean(priorResultPreview),
          },
          costs: {
            max_page_cost: Number(maxPageCost),
            max_job_cost: Number(maxJobCost),
          },
        },
      };
      if (inputIds.length > 1) {
        const response = await extractionLabApi.runMulti({
          ...basePayload,
          input_ids: inputIds,
          multi_document_mode: multiDocumentMode,
        });
        return response.results;
      }
      return [await extractionLabApi.run(basePayload)];
    },
    onSuccess: (data) => {
      setResults((current) => [...data, ...current.filter((item) => !data.some((result) => result.run_id === item.run_id))].slice(0, 12));
      setSelectedResultId(data[0]?.run_id ?? "");
      setPage(1);
      toast.success("Extraction finished", {
        description: `${data.length} run${data.length === 1 ? "" : "s"} completed.`,
      });
    },
    onError: (error) => toast.error("Extraction failed", { description: String(error) }),
  });

  const deleteResultM = useMutation({
    mutationFn: (runId: string) => extractionLabApi.deleteResult(runId),
    onSuccess: (data) => {
      setResults((current) => {
        const next = current.filter((item) => item.run_id !== data.deleted_run_id);
        if (selectedResultId === data.deleted_run_id) {
          const nextSelected = next[0]?.run_id ?? "";
          setSelectedResultId(nextSelected);
        }
        return next;
      });
      qc.invalidateQueries({ queryKey: ["extraction-lab-history"] });
      toast.success("Extraction result deleted");
    },
    onError: (error) => toast.error("Delete failed", { description: String(error) }),
  });

  const generateSchemaM = useMutation({
    mutationFn: () => {
      const inputIds = selectedInputIds.length ? selectedInputIds : selectedInputId ? [selectedInputId] : [];
      return extractionLabApi.generateSchema({
        input_ids: inputIds,
        natural_language_query: naturalLanguageQuery.trim() || null,
        parser_id: parserId,
        multi_document_mode: multiDocumentMode,
        chunking_strategy: chunkingStrategy,
        chunk_size: chunkSize,
        chunk_overlap: chunkOverlap,
        max_pages: maxPages,
        preview_chars: 8000,
      });
    },
    onSuccess: (data) => {
      setSchema(cloneSchema(data.schema_definition));
      setSchemaJsonError(null);
      toast.success("Schema generated", {
        description: data.warnings.length ? data.warnings[0] : "Review the generated fields before extraction.",
      });
    },
    onError: (error) => toast.error("Schema generation failed", { description: String(error) }),
  });

  const saveSchemaM = useMutation({
    mutationFn: () => extractionLabApi.saveSchema(schema),
    onSuccess: (tpl) => {
      qc.invalidateQueries({ queryKey: ["extraction-lab-schemas"] });
      toast.success("Template saved", { description: `"${tpl.label}" added to templates.` });
    },
    onError: (error) => toast.error("Failed to save template", { description: String(error) }),
  });

  const deleteSchemaM = useMutation({
    mutationFn: (id: string) => extractionLabApi.deleteSchema(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["extraction-lab-schemas"] });
      toast.success("Template deleted", { description: "Template was removed." });
    },
    onError: (error) => toast.error("Failed to delete template", { description: String(error) }),
  });

  const canRun =
    (selectedInputIds.length > 0 || Boolean(selectedInputId)) &&
    schema.fields.length > 0 &&
    schema.fields.every((field) => field.key.trim()) &&
    !runM.isPending;
  const canGenerateSchema =
    (selectedInputIds.length > 0 || Boolean(selectedInputId) || Boolean(naturalLanguageQuery.trim())) &&
    !generateSchemaM.isPending;

  function updateField(id: string, patch: Partial<ExtractionSchemaField>) {
    setSchema((current) => ({
      ...current,
      fields: updateFieldTree(current.fields, id, patch),
    }));
  }

  function updateFieldLabel(id: string, label: string) {
    setSchema((current) => ({
      ...current,
      fields: updateFieldLabelTree(current.fields, id, label),
    }));
  }

  function addField() {
    setSchema((current) => ({
      ...current,
      fields: [...current.fields, makeField("new_field", "New Field")],
    }));
  }

  function resetManualSchema() {
    setSchema(cloneSchema(EMPTY_SCHEMA));
    setNaturalLanguageQuery("");
    setSchemaJsonError(null);
  }

  function removeField(id: string) {
    setSchema((current) => ({
      ...current,
      fields: removeFieldFromTree(current.fields, id),
    }));
  }

  function addChildField(parentId: string) {
    setSchema((current) => ({
      ...current,
      fields: addChildFieldToTree(current.fields, parentId),
    }));
  }

  function bulkUpdateFields(patch: Partial<ExtractionSchemaField>) {
    setSchema((current) => ({
      ...current,
      fields: mapFieldTree(current.fields, (field) => ({ ...field, ...patch })),
    }));
  }

  function selectResultField(fieldKey: string | null) {
    setSelectedFieldKey(fieldKey);
    if (!fieldKey) return;
    const field = result?.fields.find((item) => item.key === fieldKey);
    const firstEvidencePage = field?.evidence.find((item) => Number.isFinite(item.page))?.page;
    if (firstEvidencePage) setPage(firstEvidencePage);
  }

  function updateResultFieldValue(fieldKey: string, value: unknown, sourceField: ExtractionFieldResult) {
    const targetRunId = result?.run_id;
    if (!targetRunId) return;
    setResults((current) =>
      current.map((item) => {
        if (item.run_id !== targetRunId) return item;
        const fieldExists = item.fields.some((field) => field.key === fieldKey);
        const nextFields = fieldExists
          ? item.fields.map((field) =>
              field.key === fieldKey
                ? {
                    ...field,
                    value,
                    valid: true,
                    validation_message: null,
                  }
                : field,
            )
          : [
              ...item.fields,
              {
                ...sourceField,
                value,
                raw_value: sourceField.raw_value ?? sourceField.value,
                valid: true,
                validation_message: null,
              },
            ];
        return {
          ...item,
          data: setResultDataValue(item.data, fieldKey, value),
          fields: nextFields,
        };
      }),
    );
  }

  function applyJsonSchema() {
    try {
      const parsed = normalizeSchema(JSON.parse(schemaJson));
      setSchema(parsed);
      setSchemaJsonError(null);
      toast.success("JSON schema applied");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Invalid JSON schema";
      setSchemaJsonError(message);
      toast.error("Schema JSON is invalid", { description: message });
    }
  }

  function handleSchemaJsonChange(value: string) {
    setSchemaJson(value);
    try {
      const parsed = normalizeSchema(JSON.parse(value));
      setSchema(parsed);
      setSchemaJsonError(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Invalid JSON schema";
      setSchemaJsonError(message);
    }
  }

  function draftSchemaFromNaturalLanguage() {
    const query = naturalLanguageQuery.trim();
    if (!query) {
      toast.error("Describe what to extract first");
      return;
    }
    setSchema(schemaFromNaturalLanguage(query));
    setSchemaJsonError(null);
    toast.success("Draft schema created", { description: "Review the builder fields before running extraction." });
  }

  function selectPrimaryInput(inputId: string) {
    setSelectedInputId(inputId);
    setSelectedInputIds((current) => (current.includes(inputId) ? current : [inputId]));
  }

  function toggleInput(inputId: string, checked: boolean) {
    const next = checked ? Array.from(new Set([...selectedInputIds, inputId])) : selectedInputIds.filter((id) => id !== inputId);
    setSelectedInputIds(next);
    if (checked) setSelectedInputId(inputId);
    if (!checked && selectedInputId === inputId) setSelectedInputId(next[0] ?? "");
  }

  function toggleAll(checked: boolean) {
    setSelectedInputIds(checked ? inputs.map((i) => i.id) : []);
    if (!checked) setSelectedInputId("");
    else if (inputs.length > 0) setSelectedInputId(inputs[0].id);
  }

  function removeInput(inputId: string) {
    setSelectedInputIds((current) => {
      const next = current.filter((id) => id !== inputId);
      if (selectedInputId === inputId) setSelectedInputId(next[0] ?? "");
      return next;
    });
    extractionLabApi.deleteInput(inputId).then(() => {
      qc.invalidateQueries({ queryKey: ["extraction-lab-inputs"] });
    });
  }

  return (
    <div className="mx-auto w-full max-w-[1680px] space-y-4">
      {/* Compact top bar — gold-standard style */}
      <div className="flex items-center gap-3 border-b border-border/60 pb-3">
        <Button
          variant="outline"
          size="sm"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploadM.isPending}
          className="gap-1.5 text-sm"
        >
          {uploadM.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}
          Upload
        </Button>
        {selectedInputIds.length > 0 && (
          <span className="text-xs text-muted-foreground">
            {selectedInputIds.length} file{selectedInputIds.length > 1 ? "s" : ""}
          </span>
        )}
        {selectedInput && (
          <button
            type="button"
            className="flex items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted/70"
            onClick={() => {
              navigator.clipboard.writeText(selectedInput.name);
              toast.success("Filename copied");
            }}
            title="Click to copy filename"
          >
            <FileText className="size-3 shrink-0 text-muted-foreground" />
            <span className="max-w-[220px] truncate">{selectedInput.name}</span>
            <Copy className="size-3 shrink-0 text-muted-foreground" />
          </button>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button onClick={() => runM.mutate()} disabled={!canRun} size="sm">
            {runM.isPending ? <RefreshCw className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            Run Extract
          </Button>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.doc,.docx,.txt,.md,.csv,.tsv,.json"
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            if (files.length) uploadM.mutate(files);
            event.target.value = "";
          }}
        />
      </div>

      {runM.isPending ? (
        <ProcessingPanel input={selectedInput} parserId={parserId} />
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(400px,0.9fr)_minmax(620px,1.2fr)]">
        <div className="space-y-4">
          {activeTab === "results" && result ? (
            <EvidenceDocumentViewer
              input={result.input}
              chunks={result.chunks}
              fields={result.fields}
              page={boundedPage}
              pageCount={pageCount}
              zoom={resultZoom}
              selectedFieldKey={selectedFieldKey}
              onPageChange={setPage}
              onZoomChange={setResultZoom}
              onFieldSelect={selectResultField}
              hoveredFieldKey={hoveredFieldKey}
              onHoverField={setHoveredFieldKey}
            />
          ) : (
            <>
              <DocumentPanel
                inputs={inputs}
                selectedInputId={selectedInputId}
                selectedInputIds={selectedInputIds}
                selectedInput={selectedInput}
                multiDocumentMode={multiDocumentMode}
                inputsLoading={inputsQ.isLoading}
                onSelectInput={selectPrimaryInput}
                onToggleInput={toggleInput}
                onToggleAll={toggleAll}
                onRemoveInput={removeInput}
                onMultiDocumentModeChange={setMultiDocumentMode}
                onUpload={() => fileInputRef.current?.click()}
                page={boundedPage}
                pageCount={pageCount}
                onPageChange={setPage}
              />
              <SourcePreviewPanel
                input={selectedInput}
                page={boundedPage}
                pageCount={pageCount}
              />
            </>
          )}
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="min-w-0">
          {/* Gold-standard tab bar: clean underline tabs + badges on same row */}
          <div className="mb-4 flex items-center justify-between border-b border-border/60">
            <TabsList className="h-auto gap-0 rounded-none bg-transparent p-0">
              <TabsTrigger
                value="build"
                className="rounded-none border-b-2 border-transparent px-4 py-2.5 text-sm font-medium text-muted-foreground transition-colors data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none"
              >
                Build
              </TabsTrigger>
              <TabsTrigger
                value="results"
                disabled={!result}
                className="rounded-none border-b-2 border-transparent px-4 py-2.5 text-sm font-medium text-muted-foreground transition-colors data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none"
              >
                Results
              </TabsTrigger>
              <TabsTrigger
                value="code"
                className="rounded-none border-b-2 border-transparent px-4 py-2.5 text-sm font-medium text-muted-foreground transition-colors data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none"
              >
                Code
              </TabsTrigger>
              <TabsTrigger
                value="history"
                className="rounded-none border-b-2 border-transparent px-4 py-2.5 text-sm font-medium text-muted-foreground transition-colors data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none"
              >
                History
              </TabsTrigger>
            </TabsList>
            <div className="flex flex-wrap items-center gap-1.5 pb-1">
              {result ? (
                <>
                  <Badge tone={result.validation_errors.length ? "amber" : "emerald"}>
                    {result.validation_errors.length ? `${result.validation_errors.length} validation issue` : "Valid"}
                  </Badge>
                  {result.stats.retrieval_mode && (
                    <Badge
                      tone={
                        result.stats.retrieval_mode === "full_pipeline"
                          ? "emerald"
                          : result.stats.retrieval_mode === "dense_only"
                          ? "teal"
                          : result.stats.retrieval_mode === "bm25_only"
                          ? "amber"
                          : result.stats.retrieval_mode === "fts_fallback"
                          ? "rose"
                          : "slate"
                      }
                    >
                      {result.stats.retrieval_mode === "full_pipeline"
                        ? "Hybrid (Dense + BM25)"
                        : result.stats.retrieval_mode === "dense_only"
                        ? "Dense Only"
                        : result.stats.retrieval_mode === "bm25_only"
                        ? "BM25 Only"
                        : result.stats.retrieval_mode === "fts_fallback"
                        ? "FTS Fallback"
                        : result.stats.retrieval_mode === "in_memory"
                        ? "In-Memory Preview"
                        : result.stats.retrieval_mode}
                    </Badge>
                  )}
                  <Badge tone="slate">{result.stats.chunks} chunks</Badge>
                  <Badge tone={result.extraction_tier === "agentic" ? "violet" : "slate"}>
                    {result.extraction_tier === "agentic" ? "Agentic" : "Cost effective"}
                  </Badge>
                </>
              ) : (
                <Badge tone="slate">No run yet</Badge>
              )}
            </div>
          </div>

          <TabsContent value="build" className="mt-0 space-y-4">
            <ExtractTierPanel
              advancedOpen={advancedOpen}
              onAdvancedOpenChange={setAdvancedOpen}
              
              // Model Settings
              modelTier={modelTier}
              onModelTierChange={setModelTier}
              temperature={temperature}
              onTemperatureChange={setTemperature}
              maxTokens={maxTokens}
              onMaxTokensChange={setMaxTokens}

              // Retrieval Settings
              denseCandidateLimit={denseCandidateLimit}
              onDenseCandidateLimitChange={setDenseCandidateLimit}
              sparseCandidateLimit={sparseCandidateLimit}
              onSparseCandidateLimitChange={setSparseCandidateLimit}
              rankFusionConstant={rankFusionConstant}
              onRankFusionConstantChange={setRankFusionConstant}
              scalarChunkLimit={scalarChunkLimit}
              onScalarChunkLimitChange={setScalarChunkLimit}
              narrativeChunkLimit={narrativeChunkLimit}
              onNarrativeChunkLimitChange={setNarrativeChunkLimit}
              maxChunkLimit={maxChunkLimit}
              onMaxChunkLimitChange={setMaxChunkLimit}
              retryChunkExpansion={retryChunkExpansion}
              onRetryChunkExpansionChange={setRetryChunkExpansion}

              // Queries Settings
              emptyResultsMaxRetry={emptyResultsMaxRetry}
              onEmptyResultsMaxRetryChange={setEmptyResultsMaxRetry}
              queryMinWords={queryMinWords}
              onQueryMinWordsChange={setQueryMinWords}
              queryMaxWords={queryMaxWords}
              onQueryMaxWordsChange={setQueryMaxWords}
              priorResultPreview={priorResultPreview}
              onPriorResultPreviewChange={setPriorResultPreview}

              // Cost Settings
              maxPageCost={maxPageCost}
              onMaxPageCostChange={setMaxPageCost}
              maxJobCost={maxJobCost}
              onMaxJobCostChange={setMaxJobCost}

              // Webhooks
              webhooksOpen={webhooksOpen}
              webhookUrl={webhookUrl}
              webhookEvents={webhookEvents}
              webhookPayloadFormat={webhookPayloadFormat}
              onWebhooksOpenChange={setWebhooksOpen}
              onWebhookUrlChange={setWebhookUrl}
              onWebhookEventsChange={setWebhookEvents}
              onWebhookPayloadFormatChange={setWebhookPayloadFormat}
            />

            <ConfigurationPanel
              parsers={compatibleParsers}
              parserId={parserId}
              parserInstalled={parserInstalled}
              maxPages={maxPages}
              maxCandidates={maxCandidates}
              chunkingStrategy={chunkingStrategy}
              chunkSize={chunkSize}
              chunkOverlap={chunkOverlap}
              latestByParser={latestByParser}
              onParserChange={setParserId}
              onMaxPagesChange={setMaxPages}
              onMaxCandidatesChange={setMaxCandidates}
              onChunkingStrategyChange={setChunkingStrategy}
              onChunkSizeChange={setChunkSize}
              onChunkOverlapChange={setChunkOverlap}
            />

            <SectionCard
              title="Schema"
              description={`${schema.fields.length} field${schema.fields.length === 1 ? "" : "s"} in ${schema.name}`}
              actions={
                <div className="flex flex-wrap items-center gap-2">
                  {schemaTemplates.map((template) => (
                    <div key={template.id} className="relative group/template flex items-center">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setSchema(cloneSchema(template.schema))}
                        title={template.filename}
                        className="pr-7 relative font-medium transition-all"
                      >
                        {template.label}
                      </Button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteSchemaM.mutate(template.id);
                        }}
                        className="absolute right-1.5 opacity-60 hover:opacity-100 text-muted-foreground hover:text-destructive p-0.5 rounded transition-all"
                        title={`Delete ${template.label} template`}
                      >
                        <X className="size-3" />
                      </button>
                    </div>
                  ))}
                  <Button size="sm" variant="outline" onClick={resetManualSchema}>
                    Manual
                  </Button>
                  {schema.fields.length > 0 && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => saveSchemaM.mutate()}
                      disabled={saveSchemaM.isPending}
                    >
                      <Save className="size-3.5" />
                      Save as Template
                    </Button>
                  )}
                  <Button size="sm" onClick={addField}>
                    <Plus className="size-3.5" />
                    Field
                  </Button>
                </div>
              }
            >
              <Tabs defaultValue="builder" className="space-y-4">
                <TabsList>
                  <TabsTrigger value="builder" className="gap-1.5">
                    <Table2 className="size-4" />
                    Builder
                  </TabsTrigger>
                  <TabsTrigger value="json" className="gap-1.5">
                    <Braces className="size-4" />
                    JSON
                  </TabsTrigger>
                </TabsList>
                <TabsContent value="builder" className="mt-0">
                  <SchemaBuilderTab
                    input={selectedInput}
                    selectedInputs={selectedInputs}
                    multiDocumentMode={multiDocumentMode}
                    prompt={naturalLanguageQuery}
                    onPromptChange={setNaturalLanguageQuery}
                    onGenerate={() => generateSchemaM.mutate()}
                    canGenerate={canGenerateSchema}
                    generating={generateSchemaM.isPending}
                    schema={schema}
                    onSchemaChange={setSchema}
                    onAddField={addField}
                    onAddChildField={addChildField}
                    onRemoveField={removeField}
                    onUpdateField={updateField}
                    onUpdateFieldLabel={updateFieldLabel}
                    onBulkUpdateFields={bulkUpdateFields}
                  />
                </TabsContent>
                <TabsContent value="json" className="mt-0 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-muted/10 px-3 py-2">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">JSON schema format</p>
                      <p className="text-xs text-muted-foreground">
                        Edit the same schema object the extraction API receives.
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => generateSchemaM.mutate()}
                      disabled={!canGenerateSchema}
                    >
                      {generateSchemaM.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />}
                      Refine with AI
                    </Button>
                  </div>
                  <Textarea
                    value={schemaJson}
                    onChange={(event) => handleSchemaJsonChange(event.target.value)}
                    className="min-h-[440px] font-mono text-xs leading-5"
                    spellCheck={false}
                  />
                  <div className="flex items-center justify-between gap-3">
                    <p className={cn("text-xs", schemaJsonError ? "text-destructive" : "text-muted-foreground")}>
                      {schemaJsonError ?? "JSON schema is ready to apply."}
                    </p>
                    <Button size="sm" onClick={applyJsonSchema}>
                      <CheckCircle2 className="size-4" />
                      Apply JSON
                    </Button>
                  </div>
                </TabsContent>
              </Tabs>
            </SectionCard>
          </TabsContent>

          <TabsContent value="results" className="mt-0">
            <ResultsPanel
              result={result}
              results={visibleResults}
              selectedResultId={selectedResultId}
              onSelectResult={setSelectedResultId}
              selectedFieldKey={selectedFieldKey}
              onFieldSelect={selectResultField}
              activeResultTab={activeResultTab}
              onActiveResultTabChange={setActiveResultTab}
              confidenceThreshold={confidenceThreshold}
              onConfidenceThresholdChange={setConfidenceThreshold}
              onFieldValueChange={updateResultFieldValue}
              hoveredFieldKey={hoveredFieldKey}
              onHoverField={setHoveredFieldKey}
              onStartOver={() => {
                setActiveTab("build");
                selectResultField(null);
              }}
              onRunAgain={() => {
                if (canRun) runM.mutate();
                selectResultField(null);
              }}
              onDeleteResult={(runId) => deleteResultM.mutate(runId)}
            />
          </TabsContent>

          <TabsContent value="code" className="mt-0">
            <CodePanel schema={schema} result={result} />
          </TabsContent>

          <TabsContent value="history" className="mt-0">
            <HistoryTab
              onLoadResult={async (runId) => {
                try {
                  toast.loading("Loading result...", { id: "load-result" });
                  const data = await extractionLabApi.getJobResult(runId);
                  setResults((current) => [data, ...current.filter((item) => item.run_id !== data.run_id)]);
                  setSelectedResultId(data.run_id);
                  setPage(1);
                  if (data.schema_definition) {
                    setSchema(cloneSchema(data.schema_definition));
                  }
                  setActiveTab("results");
                  toast.success("Result loaded", { id: "load-result" });
                } catch (err) {
                  toast.error("Failed to load result", { id: "load-result", description: String(err) });
                }
              }}
              onDeleteResult={(runId) => deleteResultM.mutate(runId)}
            />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

function DocumentPanel({
  inputs,
  selectedInputId,
  selectedInputIds,
  selectedInput,
  multiDocumentMode,
  inputsLoading,
  onSelectInput,
  onToggleInput,
  onToggleAll,
  onRemoveInput,
  onMultiDocumentModeChange,
  onUpload,
  page,
  pageCount,
  onPageChange,
}: {
  inputs: ParserInputInfo[];
  selectedInputId: string;
  selectedInputIds: string[];
  selectedInput: ParserInputInfo | undefined;
  multiDocumentMode: MultiDocumentMode;
  inputsLoading: boolean;
  onSelectInput: (inputId: string) => void;
  onToggleInput: (inputId: string, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
  onRemoveInput: (inputId: string) => void;
  onMultiDocumentModeChange: (mode: MultiDocumentMode) => void;
  onUpload: () => void;
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
}) {
  return (
    <SectionCard
      title="Document"
      description="PDF, image, document, or text source"
      actions={
        <Button size="sm" variant="outline" onClick={onUpload}>
          <Upload className="size-3.5" />
          Upload
        </Button>
      }
    >
      <div className="space-y-4">
        <Select value={selectedInputId} onValueChange={onSelectInput}>
          <SelectTrigger className="w-full">
            <SelectValue placeholder={inputsLoading ? "Loading inputs..." : "Select an input"} />
          </SelectTrigger>
          <SelectContent>
            {inputs.map((input) => (
              <SelectItem key={input.id} value={input.id}>
                <span className="flex min-w-0 items-center gap-2">
                  <FileText className="size-4" />
                  <span className="truncate">{input.name}</span>
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {inputs.length > 1 ? (
          <div className="space-y-2 rounded-lg border border-border bg-muted/10 p-3">
            <div className="flex items-center justify-between gap-2">
              <Label className="text-xs text-muted-foreground">Documents to extract</Label>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="text-xs text-muted-foreground underline-offset-4 hover:underline"
                  onClick={() => onToggleAll(selectedInputIds.length !== inputs.length)}
                >
                  {selectedInputIds.length === inputs.length ? "Deselect all" : "Select all"}
                </button>
                <Badge tone="slate">{selectedInputIds.length} selected</Badge>
              </div>
            </div>
            <div className="max-h-40 space-y-2 overflow-auto pr-1">
              {inputs.map((input) => (
                <div key={input.id} className="flex items-center gap-2 rounded-md px-1 py-1 text-sm hover:bg-muted/40">
                  <Checkbox
                    checked={selectedInputIds.includes(input.id)}
                    onCheckedChange={(checked) => onToggleInput(input.id, checked === true)}
                  />
                  <span className="min-w-0 flex-1 truncate">{input.name}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{input.input_type}</span>
                  <button
                    type="button"
                    className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                    onClick={() => onRemoveInput(input.id)}
                    title="Remove document"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              ))}
            </div>
            {selectedInputIds.length > 1 ? (
              <div className="grid grid-cols-2 gap-1 rounded-lg border border-border bg-background p-1">
                <Button
                  type="button"
                  size="sm"
                  variant={multiDocumentMode === "per_document" ? "default" : "ghost"}
                  onClick={() => onMultiDocumentModeChange("per_document")}
                >
                  Per document
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={multiDocumentMode === "cross_document" ? "default" : "ghost"}
                  onClick={() => onMultiDocumentModeChange("cross_document")}
                >
                  Cross-page
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}

        {selectedInput ? (
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="teal">{selectedInput.input_type.toUpperCase()}</Badge>
            <Badge tone="slate">{formatBytes(selectedInput.size_bytes)}</Badge>
            <Badge tone="slate" className="max-w-full">
              <span className="truncate">{selectedInput.name}</span>
            </Badge>
          </div>
        ) : (
          <EmptyState
            icon={<Database className="size-5" />}
            title="No input selected"
            description="Upload or choose a parser input."
            className="py-8"
          />
        )}

        <div className="flex items-center justify-between rounded-lg border border-border bg-muted/20 px-2 py-1.5">
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              disabled={page <= 1}
              onClick={() => onPageChange(Math.max(1, page - 1))}
              title="Previous page"
            >
              <ChevronLeft className="size-4" />
            </Button>
            <span className="min-w-14 text-center text-sm font-medium tabular-nums">
              {page} / {pageCount}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              disabled={page >= pageCount}
              onClick={() => onPageChange(Math.min(pageCount, page + 1))}
              title="Next page"
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
          <span className="text-xs text-muted-foreground">Source preview</span>
        </div>
      </div>
    </SectionCard>
  );
}

function SourcePreviewPanel({
  input,
  page,
  pageCount,
}: {
  input: ParserInputInfo | undefined;
  page: number;
  pageCount: number;
}) {
  const [pageImageFailed, setPageImageFailed] = React.useState(false);
  React.useEffect(() => {
    setPageImageFailed(false);
  }, [input?.id, page]);

  if (!input) {
    return (
      <SectionCard title="Preview" noBodyPadding>
        <EmptyState
          icon={<FileText className="size-5" />}
          title="Choose a source"
          description="The document preview appears here."
          className="m-5 min-h-[520px]"
        />
      </SectionCard>
    );
  }

  const canRenderPage = input.input_type === "pdf" || input.input_type === "image";
  const pageImageUrl = parserBenchmarksApi.pageImageUrl(input.id, input.input_type === "image" ? 1 : page, 1.5);
  const previewUrl = parserBenchmarksApi.previewUrl(input.id);

  return (
    <SectionCard title="Preview" noBodyPadding>
      <div className="border-b border-border/70 bg-background px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          {/* Gold-standard: filename as a styled chip with copy icon */}
          <button
            type="button"
            className="flex items-center gap-1.5 rounded-md border border-border bg-muted/30 px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted/60 min-w-0"
            onClick={() => {
              navigator.clipboard.writeText(input.name);
            }}
            title="Click to copy filename"
          >
            <span className="max-w-[200px] truncate">{input.name}</span>
            <Copy className="size-3 shrink-0 text-muted-foreground" />
          </button>
          <div className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
            <span className="tabular-nums">{page}</span>
            <span>of</span>
            <span className="tabular-nums">{pageCount}</span>
          </div>
        </div>
      </div>
      <div className="h-[680px] overflow-auto bg-muted/30 p-5">
        {canRenderPage && !pageImageFailed ? (
          <div className="mx-auto w-full max-w-[760px] rounded-sm bg-white shadow-sm ring-1 ring-border">
            <img
              src={pageImageUrl}
              alt={`${input.name} page ${page}`}
              className="block w-full select-none"
              draggable={false}
              onError={() => setPageImageFailed(true)}
            />
          </div>
        ) : input.input_type === "pdf" ? (
          <iframe
            title={`${input.name} preview`}
            src={`${previewUrl}#toolbar=1&navpanes=0&scrollbar=1&page=${page}`}
            className="h-full w-full rounded-lg border border-border bg-background"
          />
        ) : input.input_type === "text" ? (
          <TextPreview input={input} />
        ) : (
          <EmptyState
            icon={<AlertTriangle className="size-5" />}
            title="Preview unavailable"
            description="This file type can still be parsed when a compatible parser is available."
            className="h-full"
          />
        )}
      </div>
    </SectionCard>
  );
}

function TextPreview({ input }: { input: ParserInputInfo }) {
  const textQ = useQuery({
    queryKey: ["parser-preview-text", input.id],
    queryFn: () => parserBenchmarksApi.previewText(input.id),
  });
  return (
    <ScrollArea className="h-full rounded-lg border border-border bg-background">
      <pre className="whitespace-pre-wrap p-4 text-xs leading-5">
        {textQ.isLoading ? "Loading..." : textQ.data ?? "No text preview."}
      </pre>
    </ScrollArea>
  );
}

function ExtractTierPanel({
  advancedOpen,
  onAdvancedOpenChange,
  
  // Model Settings
  modelTier,
  onModelTierChange,
  temperature,
  onTemperatureChange,
  maxTokens,
  onMaxTokensChange,

  // Retrieval Settings
  denseCandidateLimit,
  onDenseCandidateLimitChange,
  sparseCandidateLimit,
  onSparseCandidateLimitChange,
  rankFusionConstant,
  onRankFusionConstantChange,
  scalarChunkLimit,
  onScalarChunkLimitChange,
  narrativeChunkLimit,
  onNarrativeChunkLimitChange,
  maxChunkLimit,
  onMaxChunkLimitChange,
  retryChunkExpansion,
  onRetryChunkExpansionChange,

  // Queries Settings
  emptyResultsMaxRetry,
  onEmptyResultsMaxRetryChange,
  queryMinWords,
  onQueryMinWordsChange,
  queryMaxWords,
  onQueryMaxWordsChange,
  priorResultPreview,
  onPriorResultPreviewChange,

  // Cost Settings
  maxPageCost,
  onMaxPageCostChange,
  maxJobCost,
  onMaxJobCostChange,

  // Webhooks
  webhooksOpen,
  webhookUrl,
  webhookEvents,
  webhookPayloadFormat,
  onWebhooksOpenChange,
  onWebhookUrlChange,
  onWebhookEventsChange,
  onWebhookPayloadFormatChange,
}: {
  advancedOpen: boolean;
  onAdvancedOpenChange: (open: boolean) => void;

  // Model Settings
  modelTier: string;
  onModelTierChange: (value: string) => void;
  temperature: number;
  onTemperatureChange: (value: number) => void;
  maxTokens: number;
  onMaxTokensChange: (value: number) => void;

  // Retrieval Settings
  denseCandidateLimit: number;
  onDenseCandidateLimitChange: (value: number) => void;
  sparseCandidateLimit: number;
  onSparseCandidateLimitChange: (value: number) => void;
  rankFusionConstant: number;
  onRankFusionConstantChange: (value: number) => void;
  scalarChunkLimit: number;
  onScalarChunkLimitChange: (value: number) => void;
  narrativeChunkLimit: number;
  onNarrativeChunkLimitChange: (value: number) => void;
  maxChunkLimit: number;
  onMaxChunkLimitChange: (value: number) => void;
  retryChunkExpansion: number;
  onRetryChunkExpansionChange: (value: number) => void;

  // Queries Settings
  emptyResultsMaxRetry: number;
  onEmptyResultsMaxRetryChange: (value: number) => void;
  queryMinWords: number;
  onQueryMinWordsChange: (value: number) => void;
  queryMaxWords: number;
  onQueryMaxWordsChange: (value: number) => void;
  priorResultPreview: boolean;
  onPriorResultPreviewChange: (value: boolean) => void;

  // Cost Settings
  maxPageCost: number;
  onMaxPageCostChange: (value: number) => void;
  maxJobCost: number;
  onMaxJobCostChange: (value: number) => void;

  // Webhooks
  webhooksOpen: boolean;
  webhookUrl: string;
  webhookEvents: string;
  webhookPayloadFormat: string;
  onWebhooksOpenChange: (open: boolean) => void;
  onWebhookUrlChange: (value: string) => void;
  onWebhookEventsChange: (value: string) => void;
  onWebhookPayloadFormatChange: (value: string) => void;
}) {
  return (
    <SectionCard title="Extraction Settings" description="Agentic extraction for complex evidence, tables, and schema reasoning.">
      <div className="space-y-5">

        <div className="border-t border-border/70 pt-3">
          <button
            type="button"
            className="flex w-full items-center justify-between gap-3 py-2 text-left"
            onClick={() => onAdvancedOpenChange(!advancedOpen)}
          >
            <span className="text-base font-semibold text-muted-foreground">Advanced options</span>
            <ChevronDown className={cn("size-5 text-muted-foreground transition", advancedOpen && "rotate-180")} />
          </button>

          {advancedOpen ? (
            <div className="space-y-6 pt-4">
              {/* MODEL SETTINGS CARD */}
              <div className="rounded-lg border border-border/70 bg-card p-4 space-y-4 shadow-sm">
                <h4 className="text-base font-bold text-foreground">Model settings</h4>
                
                <div className="space-y-2">
                  <Label className="text-sm font-semibold">Model tier</Label>
                  <Select value={modelTier} onValueChange={onModelTierChange}>
                    <SelectTrigger className="h-10 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="cost_effective">Cost Effective (Gemini 2.5 Flash)</SelectItem>
                      <SelectItem value="speed">Speed (GPT-4o Mini)</SelectItem>
                      <SelectItem value="balanced">Balanced (GPT-4o)</SelectItem>
                      <SelectItem value="quality">Quality (Gemini 2.5 Pro)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label className="text-sm font-semibold">Temperature</Label>
                    <Input
                      type="number"
                      min={0.0}
                      max={1.0}
                      step={0.1}
                      value={temperature}
                      onChange={(e) => onTemperatureChange(parseFloat(e.target.value) || 0.0)}
                      className="h-10 text-sm"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label className="text-sm font-semibold">Max tokens</Label>
                    <Input
                      type="number"
                      value={maxTokens}
                      onChange={(e) => onMaxTokensChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                </div>
              </div>

              {/* RETRIEVAL SETTINGS CARD */}
              <div className="rounded-lg border border-border/70 bg-card p-4 space-y-4 shadow-sm">
                <h4 className="text-base font-bold text-foreground">Retrieval settings</h4>
                
                <div className="grid grid-cols-3 gap-4">
                  <div className="space-y-2">
                    <Label className="text-xs font-semibold">Dense candidates</Label>
                    <Input
                      type="number"
                      value={denseCandidateLimit}
                      onChange={(e) => onDenseCandidateLimitChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label className="text-xs font-semibold">Sparse candidates</Label>
                    <Input
                      type="number"
                      value={sparseCandidateLimit}
                      onChange={(e) => onSparseCandidateLimitChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label className="text-xs font-semibold">Fusion constant</Label>
                    <Input
                      type="number"
                      value={rankFusionConstant}
                      onChange={(e) => onRankFusionConstantChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label className="text-sm font-semibold">Scalar chunk limit</Label>
                    <Input
                      type="number"
                      value={scalarChunkLimit}
                      onChange={(e) => onScalarChunkLimitChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label className="text-sm font-semibold">Narrative chunk limit</Label>
                    <Input
                      type="number"
                      value={narrativeChunkLimit}
                      onChange={(e) => onNarrativeChunkLimitChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label className="text-sm font-semibold">Max chunk limit</Label>
                    <Input
                      type="number"
                      value={maxChunkLimit}
                      onChange={(e) => onMaxChunkLimitChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label className="text-sm font-semibold">Retry expansion</Label>
                    <Input
                      type="number"
                      value={retryChunkExpansion}
                      onChange={(e) => onRetryChunkExpansionChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                </div>
              </div>

              {/* QUERIES SETTINGS CARD */}
              <div className="rounded-lg border border-border/70 bg-card p-4 space-y-4 shadow-sm">
                <h4 className="text-base font-bold text-foreground">Queries settings</h4>
                
                <div className="grid grid-cols-3 gap-4">
                  <div className="space-y-2">
                    <Label className="text-xs font-semibold">Max empty retries</Label>
                    <Input
                      type="number"
                      value={emptyResultsMaxRetry}
                      onChange={(e) => onEmptyResultsMaxRetryChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label className="text-xs font-semibold">Min query words</Label>
                    <Input
                      type="number"
                      value={queryMinWords}
                      onChange={(e) => onQueryMinWordsChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label className="text-xs font-semibold">Max query words</Label>
                    <Input
                      type="number"
                      value={queryMaxWords}
                      onChange={(e) => onQueryMaxWordsChange(parseInt(e.target.value) || 0)}
                      className="h-10 text-sm"
                    />
                  </div>
                </div>

                <div className="pt-2">
                  <ToggleRow
                    label="Prior result preview"
                    checked={priorResultPreview}
                    onCheckedChange={onPriorResultPreviewChange}
                  />
                </div>
              </div>
            </div>
          ) : null}
        </div>

        <div className="border-t border-border/70 pt-3">
          <button
            type="button"
            className="flex w-full items-center justify-between gap-3 py-2 text-left"
            onClick={() => onWebhooksOpenChange(!webhooksOpen)}
          >
            <span className="text-base font-semibold">Webhooks</span>
            <ChevronDown className={cn("size-5 text-muted-foreground transition", webhooksOpen && "rotate-180")} />
          </button>

          {webhooksOpen ? (
            <div className="space-y-5 pt-4">
              <p className="text-sm text-muted-foreground">
                Get notified at your own endpoint when an extract job finishes.{" "}
                <span className="inline-flex items-center gap-1 font-medium text-primary">
                  Webhook docs <ExternalLink className="size-3.5" />
                </span>
              </p>
              <FieldWithHelp label="Webhook URL">
                <Input
                  value={webhookUrl}
                  onChange={(event) => onWebhookUrlChange(event.target.value)}
                  placeholder="https://example.com/webhook"
                  className="h-12 text-base"
                />
              </FieldWithHelp>
              <FieldWithHelp label="Events">
                <Select value={webhookEvents} onValueChange={onWebhookEventsChange}>
                  <SelectTrigger className="h-12 text-base">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All events</SelectItem>
                    <SelectItem value="extract.completed">Extract completed</SelectItem>
                    <SelectItem value="extract.failed">Extract failed</SelectItem>
                  </SelectContent>
                </Select>
              </FieldWithHelp>
              <FieldWithHelp label="Payload format">
                <Select value={webhookPayloadFormat} onValueChange={onWebhookPayloadFormatChange}>
                  <SelectTrigger className="h-12 text-base">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="string">string (default)</SelectItem>
                    <SelectItem value="json">JSON</SelectItem>
                  </SelectContent>
                </Select>
              </FieldWithHelp>
            </div>
          ) : null}
        </div>
      </div>
    </SectionCard>
  );
}

function ToggleRow({
  label,
  checked,
  onCheckedChange,
}: {
  label: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-4 text-base font-medium">
      <Switch checked={checked} onCheckedChange={onCheckedChange} className="scale-125" />
      {label}
    </label>
  );
}

function FieldWithHelp({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <Label className="flex items-center gap-2 text-base font-semibold">
        {label}
        <HelpCircle className="size-4 text-muted-foreground" />
      </Label>
      {children}
    </div>
  );
}

function SchemaAutoGeneratePanel({
  input,
  selectedInputs = [],
  multiDocumentMode,
  prompt,
  onPromptChange,
  onGenerate,
  canGenerate,
  generating,
}: {
  input: ParserInputInfo | undefined;
  selectedInputs: ParserInputInfo[];
  multiDocumentMode: MultiDocumentMode;
  prompt: string;
  onPromptChange: (value: string) => void;
  onGenerate: () => void;
  canGenerate: boolean;
  generating: boolean;
}) {
  const documents = selectedInputs.length ? selectedInputs : input ? [input] : [];
  return (
    <div className="space-y-4 rounded-lg border border-border bg-background p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold">Auto-Generate Schema</h3>
          <p className="text-sm text-muted-foreground">
            Provide a file, a prompt, or both. We'll combine what you give us.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button type="button" variant="outline" size="icon" className="size-9" title="Expand schema builder">
            <Maximize2 className="size-4" />
          </Button>
          <Button type="button" variant="outline" size="icon" className="size-9" title="Clear generated schema UI">
            <Trash2 className="size-4" />
          </Button>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-border">
        <div className="space-y-2 border-b border-border bg-muted/10 px-4 py-3">
          {documents.length ? (
            <>
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium uppercase text-muted-foreground">
                  {documents.length === 1 ? "Selected document" : `${documents.length} selected documents`}
                </span>
                {documents.length > 1 ? (
                  <Badge tone={multiDocumentMode === "cross_document" ? "violet" : "slate"}>
                    {multiDocumentMode === "cross_document" ? "Cross-page" : "Per document"}
                  </Badge>
                ) : null}
              </div>
              <div className="max-h-36 space-y-1 overflow-auto pr-1">
                {documents.map((document) => (
                  <div key={document.id} className="flex items-center gap-3 rounded-md bg-background px-3 py-2">
                    <FileText className="size-4 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{document.name}</span>
                    <span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
                      <Check className="size-3.5" />
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="flex items-center gap-3 rounded-md border border-dashed border-border bg-background px-3 py-3 text-muted-foreground">
              <FileText className="size-4" />
              <span className="text-sm">No document selected; prompt-only schema generation is available.</span>
            </div>
          )}
        </div>
        <div className="p-4">
          <Textarea
            value={prompt}
            onChange={(event) => onPromptChange(event.target.value)}
            placeholder="Describe the structure you want to extract (e.g. 'Person info: name, age, contact')"
            className="min-h-24 resize-none border-border text-base"
          />
        </div>
      </div>

      <div className="flex items-center justify-between gap-3">
        <Button type="button" variant="ghost">
          Back
        </Button>
        <Button type="button" onClick={onGenerate} disabled={!canGenerate}>
          {generating ? <Loader2 className="size-4 animate-spin" /> : <Wand2 className="size-4" />}
          Generate
        </Button>
      </div>
    </div>
  );
}

function ConfigurationPanel({
  parsers,
  parserId,
  parserInstalled,
  maxPages,
  maxCandidates,
  chunkingStrategy,
  chunkSize,
  chunkOverlap,
  latestByParser,
  onParserChange,
  onMaxPagesChange,
  onMaxCandidatesChange,
  onChunkingStrategyChange,
  onChunkSizeChange,
  onChunkOverlapChange,
}: {
  parsers: ParserInfo[];
  parserId: string;
  parserInstalled: boolean;
  maxPages: number;
  maxCandidates: number;
  chunkingStrategy: ChunkingStrategy;
  chunkSize: number;
  chunkOverlap: number;
  latestByParser: Map<string, LatestParserRun>;
  onParserChange: (parserId: string) => void;
  onMaxPagesChange: (maxPages: number) => void;
  onMaxCandidatesChange: (maxCandidates: number) => void;
  onChunkingStrategyChange: (strategy: ChunkingStrategy) => void;
  onChunkSizeChange: (size: number) => void;
  onChunkOverlapChange: (overlap: number) => void;
}) {
  const chunkStrategies: { value: ChunkingStrategy; label: string; hint: string }[] = [
    { value: "block", label: "Per block", hint: "One chunk per parser block/table/image." },
    { value: "table_row", label: "Per table row", hint: "Tables split into one chunk per row with exact row bounding box (recommended default)." },
    { value: "page", label: "Per page", hint: "One chunk per document page." },
    { value: "document", label: "Whole document", hint: "Single chunk for the entire document (coarsest)." },
    { value: "sliding_window", label: "Sliding window", hint: "Overlapping token windows over the full text." },
  ];
  const activeStrategy = chunkStrategies.find((s) => s.value === chunkingStrategy);
  const windowStrategy = chunkingStrategy === "sliding_window";
  return (
    <SectionCard title="Configuration" description="Parser output, chunking, and field retrieval">
      <div className="grid gap-4 md:grid-cols-3">
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Parser</Label>
          <Select value={parserId} onValueChange={onParserChange}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">Auto latest output</SelectItem>
              {parsers.map((parser) => (
                <SelectItem key={parser.id} value={parser.id} disabled={!parser.installed}>
                  {parser.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {!parserInstalled && parsers.length > 0 ? (
            <p className="text-xs text-amber-600 dark:text-amber-300">No compatible local parser is installed.</p>
          ) : null}
          <div className="flex flex-wrap gap-1.5 pt-1">
            {parsers.map((parser) => (
              <Badge key={parser.id} tone={latestByParser.has(parser.id) ? "emerald" : "amber"}>
                {parser.id}: {latestByParser.has(parser.id) ? "latest" : "none"}
              </Badge>
            ))}
          </div>
          {parserId !== "auto" && !latestByParser.has(parserId) ? (
            <p className="text-xs text-amber-600 dark:text-amber-300">Run this parser in Parse Lab before extraction.</p>
          ) : null}
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Max pages</Label>
          <Input
            type="number"
            min={1}
            max={500}
            value={maxPages}
            onChange={(event) => onMaxPagesChange(Number(event.target.value) || 1)}
          />
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Candidates / field</Label>
          <Input
            type="number"
            min={1}
            max={25}
            value={maxCandidates}
            onChange={(event) => onMaxCandidatesChange(Number(event.target.value) || 1)}
          />
        </div>
      </div>

      <div className="space-y-2 rounded-lg border border-border bg-muted/10 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Label className="text-xs text-muted-foreground">Chunking strategy</Label>
          {activeStrategy ? (
            <span className="text-xs text-muted-foreground">{activeStrategy.hint}</span>
          ) : null}
        </div>
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-5">
          {chunkStrategies.map((s) => (
            <Button
              key={s.value}
              type="button"
              size="sm"
              variant={chunkingStrategy === s.value ? "default" : "outline"}
              onClick={() => onChunkingStrategyChange(s.value)}
              title={s.hint}
            >
              {s.label}
            </Button>
          ))}
        </div>
        {windowStrategy ? (
          <div className="grid grid-cols-2 gap-3 pt-1">
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Window size (tokens)</Label>
              <Input
                type="number"
                min={64}
                max={8000}
                value={chunkSize}
                onChange={(event) => onChunkSizeChange(Number(event.target.value) || 64)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Overlap (tokens)</Label>
              <Input
                type="number"
                min={0}
                max={2048}
                value={chunkOverlap}
                onChange={(event) => onChunkOverlapChange(Number(event.target.value) || 0)}
              />
            </div>
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}

function getFieldTypeIcon(type: ExtractionFieldType, childrenLength?: number) {
  const size = "size-3.5 text-muted-foreground shrink-0";
  if (type === "object" || (type === "list" && childrenLength)) {
    return <Braces className={size} />;
  }
  if (type === "text") {
    return <Type className={size} />;
  }
  if (type === "number") {
    return <Hash className={size} />;
  }
  if (type === "boolean") {
    return <ToggleLeft className={size} />;
  }
  if (type === "list") {
    return <List className={size} />;
  }
  return <Type className={size} />;
}

function SchemaBuilder({
  schema,
  onSchemaChange,
  onAddField,
  onAddChildField,
  onRemoveField,
  onUpdateField,
  onUpdateFieldLabel,
  onBulkUpdateFields,
}: {
  schema: ExtractionLabSchema;
  onSchemaChange: (schema: ExtractionLabSchema) => void;
  onAddField: () => void;
  onAddChildField: (parentId: string) => void;
  onRemoveField: (id: string) => void;
  onUpdateField: (id: string, patch: Partial<ExtractionSchemaField>) => void;
  onUpdateFieldLabel: (id: string, label: string) => void;
  onBulkUpdateFields: (patch: Partial<ExtractionSchemaField>) => void;
}) {
  const renderRows = (fields: ExtractionSchemaField[], depth = 0): React.ReactNode[] =>
    fields.flatMap((field) => {
      const nested = field.type === "object" || field.type === "list";
      const rows: React.ReactNode[] = [
        <TableRow key={field.id} className={cn(depth > 0 && "bg-muted/20")}>
          <TableCell>
            <Input
              value={field.label}
              placeholder="Invoice Number"
              onChange={(event) => onUpdateFieldLabel(field.id, event.target.value)}
              style={{ marginLeft: depth * 18 }}
            />
          </TableCell>
          <TableCell>
            <Input
              value={field.key}
              className="font-mono text-xs"
              placeholder="invoiceNumber"
              onChange={(event) => onUpdateField(field.id, { key: event.target.value || "field" })}
            />
          </TableCell>
          <TableCell>
            <Select
              value={field.type}
              onValueChange={(value) => onUpdateField(field.id, { type: value as ExtractionFieldType })}
            >
              <SelectTrigger className="min-w-[110px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FIELD_TYPES.map((type) => (
                  <SelectItem key={type} value={type}>
                    <span className="flex items-center gap-1.5">
                      {getFieldTypeIcon(type, type === "list" ? field.children?.length : undefined)}
                      <span>
                        {type === "list" && field.children?.length ? "[OBJ]" : FIELD_TYPE_LABEL[type]}
                      </span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </TableCell>
          <TableCell>
            <Input
              value={field.description ?? ""}
              placeholder="Where this value appears"
              onChange={(event) => onUpdateField(field.id, { description: event.target.value })}
            />
          </TableCell>
          <TableCell>
            <div className="flex items-center justify-center">
              <Checkbox
                checked={field.required}
                onCheckedChange={(checked) => onUpdateField(field.id, { required: checked === true })}
                aria-label={`${field.label || field.key} required`}
              />
            </div>
          </TableCell>
          <TableCell>
            <div className="flex items-center justify-end gap-1">
              {nested ? (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 text-muted-foreground"
                  onClick={() => onAddChildField(field.id)}
                  aria-label={`Add property to ${field.label || field.key}`}
                >
                  <Plus className="size-4" />
                </Button>
              ) : null}
              <Button
                variant="ghost"
                size="icon"
                className="size-8 text-muted-foreground hover:text-destructive"
                onClick={() => onRemoveField(field.id)}
                aria-label={`Delete ${field.label || field.key}`}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          </TableCell>
        </TableRow>,
      ];
      if (nested && field.children?.length) rows.push(...renderRows(field.children, depth + 1));
      return rows;
    });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-muted/10 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-sm font-semibold">Bulk edit</span>
          <MoreVertical className="size-4 text-muted-foreground" />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => onBulkUpdateFields({ required: true })}>
            Mark all required
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => onBulkUpdateFields({ required: false })}>
            Mark optional
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => onBulkUpdateFields({ type: "text" })}>
            Set all STR
          </Button>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-[minmax(160px,240px)_1fr]">
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Model name</Label>
          <Input
            value={schema.name}
            onChange={(event) => onSchemaChange({ ...schema, name: event.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Description</Label>
          <Input
            value={schema.description ?? ""}
            onChange={(event) => onSchemaChange({ ...schema, description: event.target.value })}
          />
        </div>
      </div>

      {schema.fields.length > 0 ? (
        <div className="overflow-hidden rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-44">Field</TableHead>
                <TableHead className="min-w-40">Key</TableHead>
                <TableHead className="w-40">Type</TableHead>
                <TableHead className="min-w-56">Description</TableHead>
                <TableHead className="w-24">Required</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {renderRows(schema.fields)}
            </TableBody>
          </Table>
        </div>
      ) : (
        <EmptyState
          icon={<Table2 className="size-5" />}
          title="No fields yet"
          description="Add a field or draft one from a natural-language request."
          className="rounded-lg border border-dashed border-border py-10"
        />
      )}

      <Button variant="outline" className="w-full border-dashed" onClick={onAddField}>
        <Plus className="size-4" />
        Add field
      </Button>
    </div>
  );
}

function SchemaBuilderTab({
  input,
  selectedInputs,
  multiDocumentMode,
  prompt,
  onPromptChange,
  onGenerate,
  canGenerate,
  generating,
  schema,
  onSchemaChange,
  onAddField,
  onAddChildField,
  onRemoveField,
  onUpdateField,
  onUpdateFieldLabel,
  onBulkUpdateFields,
}: {
  input: ParserInputInfo | undefined;
  selectedInputs: ParserInputInfo[];
  multiDocumentMode: MultiDocumentMode;
  prompt: string;
  onPromptChange: (value: string) => void;
  onGenerate: () => void;
  canGenerate: boolean;
  generating: boolean;
  schema: ExtractionLabSchema;
  onSchemaChange: (schema: ExtractionLabSchema) => void;
  onAddField: () => void;
  onAddChildField: (parentId: string) => void;
  onRemoveField: (id: string) => void;
  onUpdateField: (id: string, patch: Partial<ExtractionSchemaField>) => void;
  onUpdateFieldLabel: (id: string, label: string) => void;
  onBulkUpdateFields: (patch: Partial<ExtractionSchemaField>) => void;
}) {
  const [mode, setMode] = React.useState<"auto" | "manual">("auto");

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 rounded-lg border border-border bg-muted/20 p-1">
        <Button
          type="button"
          variant={mode === "auto" ? "default" : "ghost"}
          onClick={() => setMode("auto")}
          className="gap-1.5"
        >
          <Wand2 className="size-4" />
          Auto-Generate Schema
        </Button>
        <Button
          type="button"
          variant={mode === "manual" ? "default" : "ghost"}
          onClick={() => setMode("manual")}
          className="gap-1.5"
        >
          <Table2 className="size-4" />
          Manual Schema
        </Button>
      </div>

      <div className="rounded-lg border border-border bg-muted/10 px-4 py-2 text-xs text-muted-foreground">
        {mode === "auto"
          ? "Describe what you want in natural language. We'll build a schema that fits the uploaded document."
          : "Define each field by hand. Full control over keys, types, descriptions, and required flags."}
      </div>

      {mode === "auto" ? (
        <div className="space-y-4">
          <SchemaAutoGeneratePanel
            input={input}
            selectedInputs={selectedInputs}
            multiDocumentMode={multiDocumentMode}
            prompt={prompt}
            onPromptChange={onPromptChange}
            onGenerate={onGenerate}
            canGenerate={canGenerate}
            generating={generating}
          />
          {schema.fields.length > 0 ? (
            <div className="space-y-3 rounded-lg border border-border bg-background p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-base font-semibold">Generated Schema</h3>
                  <p className="text-sm text-muted-foreground">Review and edit the generated fields before extraction.</p>
                </div>
                <Badge tone="violet">
                  {schema.fields.length} field{schema.fields.length === 1 ? "" : "s"}
                </Badge>
              </div>
              <SchemaBuilder
                schema={schema}
                onSchemaChange={onSchemaChange}
                onAddField={onAddField}
                onAddChildField={onAddChildField}
                onRemoveField={onRemoveField}
                onUpdateField={onUpdateField}
                onUpdateFieldLabel={onUpdateFieldLabel}
                onBulkUpdateFields={onBulkUpdateFields}
              />
            </div>
          ) : null}
        </div>
      ) : (
        <SchemaBuilder
          schema={schema}
          onSchemaChange={onSchemaChange}
          onAddField={onAddField}
          onAddChildField={onAddChildField}
          onRemoveField={onRemoveField}
          onUpdateField={onUpdateField}
          onUpdateFieldLabel={onUpdateFieldLabel}
          onBulkUpdateFields={onBulkUpdateFields}
        />
      )}
    </div>
  );
}

function ResultsPanel({
  result,
  results,
  selectedResultId,
  onSelectResult,
  selectedFieldKey,
  onFieldSelect,
  activeResultTab,
  onActiveResultTabChange,
  confidenceThreshold,
  onConfidenceThresholdChange,
  onFieldValueChange,
  onStartOver,
  onRunAgain,
  hoveredFieldKey,
  onHoverField,
  onDeleteResult,
}: {
  result: ExtractionRunResponse | null;
  results: ExtractionRunResponse[];
  selectedResultId: string;
  onSelectResult: (runId: string) => void;
  selectedFieldKey: string | null;
  onFieldSelect: (key: string | null) => void;
  activeResultTab: "extract" | "json";
  onActiveResultTabChange: (tab: "extract" | "json") => void;
  confidenceThreshold: number;
  onConfidenceThresholdChange: (value: number) => void;
  onFieldValueChange: (fieldKey: string, value: unknown, sourceField: ExtractionFieldResult) => void;
  onStartOver: () => void;
  onRunAgain: () => void;
  hoveredFieldKey: string | null;
  onHoverField: (key: string | null) => void;
  onDeleteResult: (runId: string) => void;
}) {
  const scrollContainerRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (selectedFieldKey) {
      const selectedEl = document.getElementById(`field-card-${selectedFieldKey}`);
      if (selectedEl) {
        selectedEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }
  }, [selectedFieldKey]);
  if (!result) {
    return (
      <SectionCard title="Results">
        <EmptyState
          icon={<FileJson className="size-5" />}
          title="No extraction result"
          description="Run extraction to populate this panel."
        />
      </SectionCard>
    );
  }

  const fieldByKey = new Map(result.fields.map((f) => [f.key, f]));
  const orderedFields: ExtractionFieldResult[] = [];
  const addedKeys = new Set<string>();

  for (const sf of result.schema_definition.fields) {
    let field = fieldByKey.get(sf.key);
    if (!field && result.data && result.data[sf.key] !== undefined) {
      field = {
        key: sf.key,
        label: sf.label || sf.key,
        type: sf.type || "text",
        required: sf.required ?? false,
        value: result.data[sf.key],
        raw_value: result.data[sf.key],
        confidence: 0.95,
        valid: true,
        validation_message: null,
        evidence: [],
      };
    }
    if (field && !addedKeys.has(field.key)) {
      orderedFields.push(field);
      addedKeys.add(field.key);
    }
  }

  // Ensure any top-level data keys from result.data not matched above are also included
  if (result.data && typeof result.data === "object") {
    for (const [key, val] of Object.entries(result.data)) {
      if (!addedKeys.has(key) && val !== undefined && val !== null) {
        orderedFields.push({
          key,
          label: key,
          type: Array.isArray(val) ? "list" : typeof val === "object" ? "object" : "text",
          required: false,
          value: val,
          raw_value: val,
          confidence: 0.95,
          valid: true,
          validation_message: null,
          evidence: [],
        });
        addedKeys.add(key);
      }
    }
  }

  const totalFields = orderedFields.length;
  const belowThreshold = orderedFields.filter((f) => Math.round(f.confidence * 100) < confidenceThreshold).length;

  const resultJson = JSON.stringify(result.data, null, 2);

  const sectionGroups = groupFieldsBySection(orderedFields, result.schema_definition);

  return (
    <div className="flex h-[825px] flex-col overflow-hidden rounded-xl border border-border bg-card">
      {/* Extraction Results header row */}
      <div className="flex items-center justify-between gap-3 border-b border-border/70 bg-background px-4 py-2 shrink-0">
        <div className="min-w-0 flex items-center gap-2">
          <h3 className="text-sm font-semibold tracking-tight shrink-0">Results</h3>
          <div className="h-4 w-px bg-border shrink-0" />
          <Select value={selectedResultId || result.run_id} onValueChange={onSelectResult}>
            <SelectTrigger className="h-7 w-[240px] border-0 bg-transparent p-0 text-xs text-muted-foreground shadow-none focus:ring-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {results.map((item) => (
                <SelectItem key={item.run_id} value={item.run_id}>
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate">{item.schema_definition.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {new Date(item.finished_at).toLocaleTimeString()}
                    </span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {/* Gold-standard: rounded pill switcher box + outline icon buttons */}
        <div className="flex items-center gap-2.5">
          <div className="inline-flex items-center rounded-xl bg-muted/60 p-0.5">
            <button
              type="button"
              onClick={() => onActiveResultTabChange("extract")}
              className={cn(
                "rounded-lg px-3.5 py-1 text-sm font-medium transition-all",
                activeResultTab === "extract"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              Extract Result
            </button>
            <button
              type="button"
              onClick={() => onActiveResultTabChange("json")}
              className={cn(
                "rounded-lg px-3.5 py-1 text-sm font-medium transition-all",
                activeResultTab === "json"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              JSON Result
            </button>
          </div>
          <button
            type="button"
            className="flex size-9 items-center justify-center rounded-xl border border-border/80 bg-background text-foreground shadow-sm transition-colors hover:bg-muted/50"
            onClick={() => {
              navigator.clipboard.writeText(activeResultTab === "json" ? resultJson : extractResultText(orderedFields));
              toast.success("Copied result");
            }}
            title="Copy result"
          >
            <Copy className="size-4" />
          </button>
          <button
            type="button"
            className="flex size-9 items-center justify-center rounded-xl border border-border/80 bg-background text-foreground shadow-sm transition-colors hover:bg-muted/50"
            onClick={() => downloadResultJson(result.data, result.schema_definition.name)}
            title="Download JSON"
          >
            <Download className="size-4" />
          </button>
          <button
            type="button"
            className="flex size-9 items-center justify-center rounded-xl border border-destructive/20 bg-destructive/10 text-destructive shadow-sm transition-colors hover:bg-destructive/20"
            onClick={() => {
              if (confirm("Are you sure you want to delete this extraction result?")) {
                onDeleteResult(selectedResultId || result.run_id);
              }
            }}
            title="Delete result"
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      </div>

      {/* Main body area, which is scrollable */}
      <div ref={scrollContainerRef} className="flex-1 min-h-0 overflow-auto p-4 space-y-4 bg-muted/5 dark:bg-background/20">
        {/* "Want to improve" info banner */}
        <div className="flex items-start gap-3 rounded-lg border border-border/60 bg-card px-4 py-3">
          <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">Want to improve your results?</p>
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
              Iterating on your field descriptions is the most effective way to improve extraction quality.
            </p>
          </div>
          <Button size="sm" variant="ghost" className="ml-auto shrink-0 text-xs">
            Got it
          </Button>
        </div>

        {activeResultTab === "extract" ? (
          <div className="space-y-4 pr-1">
            <div className="rounded-lg border border-border bg-card px-3 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs font-bold uppercase tracking-wider text-foreground">
                    {totalFields} fields
                  </span>
                  <span className={cn(
                    "font-mono text-xs font-semibold",
                    belowThreshold > 0 ? "text-amber-600 dark:text-amber-300" : "text-muted-foreground",
                  )}>
                    {belowThreshold} below {confidenceThreshold}%
                  </span>
                </div>
                <div className="flex min-w-[240px] items-center gap-3">
                  <Slider
                    value={[confidenceThreshold]}
                    min={0}
                    max={100}
                    step={1}
                    onValueChange={(value) => onConfidenceThresholdChange(value[0] ?? confidenceThreshold)}
                    className="w-40"
                  />
                  <span className="w-10 text-right text-xs font-semibold tabular-nums text-muted-foreground">
                    {confidenceThreshold}%
                  </span>
                </div>
              </div>
            </div>
            {sectionGroups.map((section, idx) => (
              <ExtractionSection
                key={section.label || `section-${idx}`}
                section={section}
                selectedFieldKey={selectedFieldKey}
                onFieldSelect={onFieldSelect}
                confidenceThreshold={confidenceThreshold}
                onFieldValueChange={onFieldValueChange}
                hoveredFieldKey={hoveredFieldKey}
                onHoverField={onHoverField}
              />
            ))}
            {sectionGroups.length === 0 ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                No fields extracted.
              </div>
            ) : null}
          </div>
        ) : (
          <div className="rounded-lg border border-border bg-muted/20">
            <pre className="whitespace-pre-wrap p-4 font-mono text-xs leading-5">
              {resultJson}
            </pre>
          </div>
        )}

        {(() => {
          const relevantErrors = result.validation_errors.filter((error) => {
            const fieldName = error.loc.split(".")[0];
            const dataVal = result.data?.[fieldName];
            // If we actually extracted non-empty data for this field (e.g. string array instead of dict array), suppress Pydantic type warnings
            if (dataVal !== undefined && dataVal !== null) {
              if (Array.isArray(dataVal)) return dataVal.length === 0;
              if (typeof dataVal === "object") return Object.keys(dataVal).length === 0;
              return false;
            }
            return true;
          });

          const relevantWarnings = result.warnings.filter((w) => {
            if (relevantErrors.length === 0 && w.includes("Some fields are missing or failed Pydantic validation")) {
              return false;
            }
            if (w.startsWith("path_b_job_id:") || w.startsWith("parser_run_id:")) {
              return false;
            }
            return true;
          });

          if (relevantErrors.length === 0 && relevantWarnings.length === 0) return null;

          return (
            <div className="mt-3 space-y-1 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
              {relevantErrors.map((error) => (
                <div key={`${error.loc}-${error.type}`} className="text-xs text-amber-700 dark:text-amber-300">
                  <span className="font-mono">{error.loc}</span>: {error.msg}
                </div>
              ))}
              {relevantWarnings.map((warning) => (
                <div key={warning} className="text-xs text-muted-foreground">{warning}</div>
              ))}
            </div>
          );
        })()}
      </div>

      <div className="flex items-center gap-2 border-t border-border/70 bg-background px-4 py-3 shrink-0">
        <Button variant="outline" size="sm" onClick={onStartOver}>
          Start over
        </Button>
        <Button variant="outline" size="sm">
          <Save className="size-3.5" />
          Save config
        </Button>
        <Button size="sm" className="ml-auto" onClick={onRunAgain}>
          Run again
          <ArrowUp className="ml-1 size-3.5" />
        </Button>
      </div>
    </div>
  );
}

type SectionGroup = { label: string; fields: ExtractionFieldResult[] };

function groupFieldsBySection(
  fields: ExtractionFieldResult[],
  schema: ExtractionLabSchema,
): SectionGroup[] {
  const fieldByKey = new Map(fields.map((f) => [f.key, f]));
  const sections: SectionGroup[] = [];
  const usedKeys = new Set<string>();

  for (const schemaField of schema.fields) {
    const children = schemaField.children ?? [];
    const parentField = fieldByKey.get(schemaField.key);

    if (children.length > 0) {
      if (parentField && (isTableField(parentField) || isListField(parentField) || isObjectField(parentField) || parentField.value === null)) {
        usedKeys.add(parentField.key);
        for (const child of children) {
          usedKeys.add(child.key);
        }
        sections.push({
          label: schemaField.label || schemaField.key,
          fields: [parentField],
        });
      } else {
        const sectionFields: ExtractionFieldResult[] = [];
        for (const child of children) {
          const childField = fieldByKey.get(child.key);
          if (childField) {
            sectionFields.push(childField);
            usedKeys.add(childField.key);
          }
        }
        if (sectionFields.length > 0) {
          if (parentField) usedKeys.add(parentField.key);
          sections.push({
            label: schemaField.label || schemaField.key,
            fields: sectionFields,
          });
        } else if (parentField) {
          usedKeys.add(parentField.key);
          sections.push({
            label: schemaField.label || schemaField.key,
            fields: [parentField],
          });
        }
      }
    } else {
      const field = fieldByKey.get(schemaField.key);
      if (field) {
        usedKeys.add(field.key);
        if (isTableField(field) || isListField(field) || isObjectField(field)) {
          sections.push({
            label: schemaField.label || schemaField.key,
            fields: [field],
          });
        } else {
          const lastGroup = sections[sections.length - 1];
          if (lastGroup && !lastGroup.label) {
            lastGroup.fields.push(field);
          } else {
            sections.push({
              label: "",
              fields: [field],
            });
          }
        }
      }
    }
  }

  const remaining = fields.filter((f) => !usedKeys.has(f.key));
  if (remaining.length > 0) {
    const lastGroup = sections[sections.length - 1];
    if (lastGroup && !lastGroup.label) {
      lastGroup.fields.push(...remaining);
    } else {
      sections.push({
        label: "",
        fields: remaining,
      });
    }
  }

  return sections;
}

function ExtractionSection({
  section,
  selectedFieldKey,
  onFieldSelect,
  confidenceThreshold,
  onFieldValueChange,
  hoveredFieldKey,
  onHoverField,
}: {
  section: SectionGroup;
  selectedFieldKey: string | null;
  onFieldSelect: (key: string | null) => void;
  confidenceThreshold: number;
  onFieldValueChange: (fieldKey: string, value: unknown, sourceField: ExtractionFieldResult) => void;
  hoveredFieldKey: string | null;
  onHoverField: (key: string | null) => void;
}) {
  return (
    <div className="py-2">
      {/* Gold-standard: plain uppercase section label, only when section has a label */}
      {section.label ? (
        <div className="pb-2 pt-4">
          <span className="font-mono text-xs font-bold uppercase tracking-wider text-primary/90">
            {section.label}
          </span>
        </div>
      ) : null}
      {/* Left border line for field indentation */}
      <div className="border-l-[3px] border-border/60 pl-5">
        {section.fields.map((field) => {
          const isSelected = selectedFieldKey === field.key;
          const isHovered = hoveredFieldKey === field.key;
          const isBelowThreshold = fieldConfidencePercent(field) < confidenceThreshold;
          const handleHover = (hover: boolean) => onHoverField(hover ? field.key : null);

          if (isTableField(field)) {
            return (
              <ExtractionTableField
                key={field.key}
                field={field}
                isSelected={isSelected}
                isBelowThreshold={isBelowThreshold}
                onSelect={() => onFieldSelect(isSelected ? null : field.key)}
                onValueChange={(value) => onFieldValueChange(field.key, value, field)}
                isHovered={isHovered}
                onHover={handleHover}
              />
            );
          }

          if (isListField(field)) {
            return (
              <ExtractionListField
                key={field.key}
                field={field}
                isSelected={isSelected}
                isBelowThreshold={isBelowThreshold}
                onSelect={() => onFieldSelect(isSelected ? null : field.key)}
                onValueChange={(value) => onFieldValueChange(field.key, value, field)}
                isHovered={isHovered}
                onHover={handleHover}
              />
            );
          }

          if (isObjectField(field)) {
            return (
              <ExtractionObjectField
                key={field.key}
                field={field}
                isSelected={isSelected}
                isBelowThreshold={isBelowThreshold}
                onSelect={() => onFieldSelect(isSelected ? null : field.key)}
                onValueChange={(value) => onFieldValueChange(field.key, value, field)}
                isHovered={isHovered}
                onHover={handleHover}
              />
            );
          }

          return (
            <ExtractionRowField
              key={field.key}
              field={field}
              isSelected={isSelected}
              isBelowThreshold={isBelowThreshold}
              onSelect={() => onFieldSelect(isSelected ? null : field.key)}
              onValueChange={(value) => onFieldValueChange(field.key, value, field)}
              isHovered={isHovered}
              onHover={handleHover}
            />
          );
        })}
      </div>
    </div>
  );
}

function isTableField(field: ExtractionFieldResult): boolean {
  const val = field.value;
  if (Array.isArray(val) && val.length > 0 && typeof val[0] === "object" && val[0] !== null) {
    return true;
  }
  return false;
}

function isListField(field: ExtractionFieldResult): boolean {
  const val = field.value;
  if (Array.isArray(val) && val.length > 0 && typeof val[0] !== "object") {
    return true;
  }
  return false;
}

function isObjectField(field: ExtractionFieldResult): boolean {
  const val = field.value;
  return typeof val === "object" && val !== null && !Array.isArray(val);
}

function isImageLikeField(field: ExtractionFieldResult): boolean {
  const key = (field.key || "").toLowerCase();
  const label = (field.label || "").toLowerCase();
  return /(image|figure|chart|visual|logo|photo|diagram)/.test(key) || /(image|figure|chart|visual|logo|photo|diagram)/.test(label);
}

function fieldConfidencePercent(field: ExtractionFieldResult): number {
  const value = Number(field.confidence);
  return Math.round((Number.isFinite(value) ? value : 0) * 100);
}

function isFieldEdited(field: ExtractionFieldResult): boolean {
  return JSON.stringify(field.value) !== JSON.stringify(field.raw_value);
}

function setResultDataValue(data: Record<string, unknown>, fieldKey: string, value: unknown): Record<string, unknown> {
  if (!fieldKey.includes(".")) {
    return { ...data, [fieldKey]: value };
  }
  const parts = fieldKey.split(".").filter(Boolean);
  if (parts.length === 0) return data;
  const root = { ...data };
  let cursor: Record<string, unknown> = root;
  for (const part of parts.slice(0, -1)) {
    const current = cursor[part];
    const next = typeof current === "object" && current !== null && !Array.isArray(current)
      ? { ...(current as Record<string, unknown>) }
      : {};
    cursor[part] = next;
    cursor = next;
  }
  cursor[parts[parts.length - 1]] = value;
  return root;
}

function parseScalarEdit(value: string, field: ExtractionFieldResult): unknown {
  if (field.type === "number" || field.type === "currency") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : value;
  }
  if (field.type === "boolean") {
    return value === "true";
  }
  return value;
}

function FieldStatusLine({
  field,
  isBelowThreshold,
}: {
  field: ExtractionFieldResult;
  isBelowThreshold: boolean;
}) {
  const confidence = fieldConfidencePercent(field);
  const edited = isFieldEdited(field);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span
        className={cn(
          "rounded-md border px-1.5 py-0.5 font-mono text-[10px] font-bold tabular-nums",
          isBelowThreshold
            ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
            : confidence >= 85
            ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
            : "border-slate-500/20 bg-slate-500/10 text-slate-600 dark:text-slate-300",
        )}
      >
        {confidence}%
      </span>
      {edited ? (
        <span className="inline-flex items-center gap-1 rounded-md border border-blue-500/20 bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700 dark:text-blue-300">
          <Pencil className="size-3" />
          Edited
        </span>
      ) : null}
      {!field.valid ? (
        <span className="rounded-md border border-rose-500/20 bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700 dark:text-rose-300">
          Invalid
        </span>
      ) : null}
    </div>
  );
}

function FieldEvidenceChips({
  field,
  onSelect,
}: {
  field: ExtractionFieldResult;
  onSelect: () => void;
}) {
  const evidence = field.evidence.slice(0, 2);
  if (evidence.length === 0) {
    return (
      <span className="text-[11px] text-muted-foreground">
        No source citation
      </span>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {evidence.map((item, index) => (
        <button
          key={`${item.chunk_id}-${index}`}
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onSelect();
          }}
          className="inline-flex max-w-full items-center gap-1 rounded-md border border-violet-500/20 bg-violet-500/10 px-2 py-1 text-[11px] font-medium text-violet-700 transition hover:bg-violet-500/15 dark:text-violet-300"
          title={item.text_preview || item.chunk_id}
        >
          <MapPin className="size-3" />
          <span>Page {item.page}</span>
          <span className="max-w-[180px] truncate text-muted-foreground">{item.type}</span>
        </button>
      ))}
    </div>
  );
}

type ImageRef = { url: string; label?: string };

function ImagePreviewGrid({ images }: { images: ImageRef[] }) {
  if (images.length === 0) return null;
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {images.slice(0, 6).map((image, index) => (
        <a
          key={`${image.url}-${index}`}
          href={parserBenchmarksApi.assetUrl(image.url)}
          target="_blank"
          rel="noreferrer"
          className="group overflow-hidden rounded-lg border border-border/70 bg-background shadow-2xs"
          onClick={(event) => event.stopPropagation()}
          title={image.label || image.url}
        >
          <img
            src={parserBenchmarksApi.assetUrl(image.url)}
            alt={image.label || `Extracted image ${index + 1}`}
            className="h-40 w-full object-contain bg-muted/30 transition-transform group-hover:scale-[1.01]"
            loading="lazy"
          />
          <div className="truncate border-t border-border/60 px-2 py-1.5 text-[11px] text-muted-foreground">
            {image.label || image.url.split("/").pop() || "Image evidence"}
          </div>
        </a>
      ))}
    </div>
  );
}

function FieldEvidenceImages({ field }: { field: ExtractionFieldResult }) {
  const images = field.evidence
    .flatMap((item) => [
      ...(item.source_url ? [{ url: item.source_url, label: `Page ${item.page} ${item.type}` }] : []),
      ...imageRefsFromValue(item.text_preview).map((image) => ({
        ...image,
        label: image.label || `Page ${item.page} ${item.type}`,
      })),
    ]);
  return <ImagePreviewGrid images={dedupeImages(images)} />;
}

function ValueWithImage({ value, compact = false }: { value: unknown; compact?: boolean }) {
  const images = imageRefsFromValue(value);
  if (images.length === 0) {
    return <span>{formatFieldValue(value)}</span>;
  }
  if (compact) {
    return (
      <div className="min-w-[120px] space-y-1">
        <img
          src={parserBenchmarksApi.assetUrl(images[0].url)}
          alt={images[0].label || "Extracted image"}
          className="h-20 w-28 rounded-md border border-border bg-muted/30 object-contain"
          loading="lazy"
        />
        <span className="block max-w-[160px] truncate text-[11px] text-muted-foreground">
          {images[0].label || images[0].url.split("/").pop()}
        </span>
      </div>
    );
  }
  return <ImagePreviewGrid images={images} />;
}

function imageRefsFromValue(value: unknown): ImageRef[] {
  if (value == null) return [];
  if (typeof value === "string") return imageRefsFromText(value);
  if (Array.isArray(value)) return dedupeImages(value.flatMap((item) => imageRefsFromValue(item)));
  if (typeof value === "object") {
    const raw = value as Record<string, unknown>;
    const direct = raw.url ?? raw.source_url ?? raw.image_url ?? raw.imageUrl ?? raw.src;
    const caption = raw.caption ?? raw.label ?? raw.title ?? raw.description;
    const refs = typeof direct === "string" ? [{ url: direct, label: typeof caption === "string" ? caption : undefined }] : [];
    return dedupeImages([...refs, ...Object.values(raw).flatMap((item) => imageRefsFromValue(item))]);
  }
  return [];
}

function imageRefsFromText(text: string): ImageRef[] {
  const refs: ImageRef[] = [];
  const markdownRe = /!\[([^\]]*)]\(([^)]+)\)/g;
  for (const match of text.matchAll(markdownRe)) {
    refs.push({ url: match[2], label: match[1] || undefined });
  }
  const urlRe = /(\/api\/parser-benchmarks\/media\/[^\s),"'<>]+|https?:\/\/[^\s),"'<>]+\.(?:png|jpe?g|gif|webp|bmp|svg)|data:image\/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+)/g;
  for (const match of text.matchAll(urlRe)) {
    refs.push({ url: match[1] });
  }
  return dedupeImages(refs);
}

function dedupeImages(images: ImageRef[]): ImageRef[] {
  const seen = new Set<string>();
  const out: ImageRef[] = [];
  for (const image of images) {
    if (!image.url || seen.has(image.url)) continue;
    seen.add(image.url);
    out.push(image);
  }
  return out;
}

function EditableScalarValue({
  field,
  multiline = false,
  onValueChange,
}: {
  field: ExtractionFieldResult;
  multiline?: boolean;
  onValueChange: (value: unknown) => void;
}) {
  const value = field.value == null ? "" : String(field.value);
  const imageRefs = isImageLikeField(field) ? imageRefsFromValue(field.value) : [];
  if (field.type === "boolean") {
    return (
      <Select
        value={String(Boolean(field.value))}
        onValueChange={(next) => onValueChange(next === "true")}
      >
        <SelectTrigger className="h-9 max-w-[180px] bg-background text-sm" onClick={(event) => event.stopPropagation()}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="true">Yes</SelectItem>
          <SelectItem value="false">No</SelectItem>
        </SelectContent>
      </Select>
    );
  }
  if (multiline) {
    return (
      <div className="space-y-2">
        <Textarea
          value={value}
          onClick={(event) => event.stopPropagation()}
          onChange={(event) => onValueChange(parseScalarEdit(event.target.value, field))}
          className="min-h-24 rounded-xl border-amber-500/30 bg-amber-500/[0.04] text-sm leading-relaxed"
        />
        <ImagePreviewGrid images={imageRefs} />
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <Input
        value={value}
        type={field.type === "number" || field.type === "currency" ? "number" : "text"}
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => onValueChange(parseScalarEdit(event.target.value, field))}
        className="h-9 bg-background text-sm font-semibold"
      />
      <ImagePreviewGrid images={imageRefs} />
    </div>
  );
}

function EditableJsonValue({
  field,
  onValueChange,
}: {
  field: ExtractionFieldResult;
  onValueChange: (value: unknown) => void;
}) {
  const [draft, setDraft] = React.useState(() => JSON.stringify(field.value, null, 2));
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    setDraft(JSON.stringify(field.value, null, 2));
    setError(null);
  }, [field.value]);

  return (
    <div className="space-y-1.5">
      <Textarea
        value={draft}
        onClick={(event) => event.stopPropagation()}
        onChange={(event) => {
          const next = event.target.value;
          setDraft(next);
          try {
            JSON.parse(next);
            setError(null);
          } catch {
            setError("Invalid JSON");
          }
        }}
        onBlur={() => {
          try {
            const parsed = JSON.parse(draft);
            setError(null);
            onValueChange(parsed);
          } catch {
            setError("Invalid JSON");
          }
        }}
        className="min-h-36 font-mono text-xs leading-5"
        spellCheck={false}
      />
      <p className={cn("text-[11px]", error ? "text-destructive" : "text-muted-foreground")}>
        {error ?? "Edit JSON to correct structured extraction data."}
      </p>
    </div>
  );
}

/**
 * Shadcn/Aceternity style row field: clean two-column layout with subtle hover states,
 * or callout card for narrative/analytical blocks.
 */
function ExtractionRowField({
  field,
  isSelected,
  isBelowThreshold,
  onSelect,
  onValueChange,
  isHovered,
  onHover,
}: {
  field: ExtractionFieldResult;
  isSelected: boolean;
  isBelowThreshold: boolean;
  onSelect: () => void;
  onValueChange: (value: unknown) => void;
  isHovered: boolean;
  onHover: (hovered: boolean) => void;
}) {
  const displayValue = formatFieldValue(field.value);
  const fieldLabel = field.key.split(".").pop()?.toUpperCase() || field.key.toUpperCase();
  const isNarrativeBlock =
    displayValue.length > 120 ||
    /basis|rationale|driver|summary|description|note|comment|review|analysis|narrative/i.test(field.key);

  return (
    <div
      id={`field-card-${field.key}`}
      onClick={onSelect}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      className={cn(
        "flex w-full items-start gap-4 border-b border-border/30 py-3.5 text-left transition-all duration-150 hover:bg-muted/20 rounded-lg px-2 scroll-mt-2",
        isSelected && "bg-muted/40 shadow-2xs ring-1 ring-violet-500/20",
        isBelowThreshold && "border-amber-500/30 bg-amber-500/[0.06] ring-1 ring-amber-500/20",
        isHovered && !isSelected && "bg-muted/10",
      )}
      role="button"
      tabIndex={0}
    >
      {/* Field name label */}
      <div className="min-w-0 shrink-0 space-y-2" style={{ width: "30%" }}>
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            {fieldLabel}
          </span>
          {field.required ? (
            <span className="shrink-0 rounded-md border border-violet-500/20 bg-violet-500/10 px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase text-violet-600 dark:text-violet-400">
              REQUIRED
            </span>
          ) : null}
        </div>
        <FieldStatusLine field={field} isBelowThreshold={isBelowThreshold} />
      </div>
      {/* Value */}
      <div className="min-w-0 flex-1 space-y-2">
        <EditableScalarValue field={field} multiline={isNarrativeBlock} onValueChange={onValueChange} />
        <FieldEvidenceImages field={field} />
        <FieldEvidenceChips field={field} onSelect={onSelect} />
      </div>
    </div>
  );
}

/**
 * Shadcn/Aceternity style object field: renders dictionaries (e.g. issuer, financialPositions, stakeholders)
 * as sleek glassmorphic structured cards with nested tables, badge clouds, and key-value rows.
 */
function ExtractionObjectField({
  field,
  isSelected,
  isBelowThreshold,
  onSelect,
  onValueChange,
  isHovered,
  onHover,
}: {
  field: ExtractionFieldResult;
  isSelected: boolean;
  isBelowThreshold: boolean;
  onSelect: () => void;
  onValueChange: (value: unknown) => void;
  isHovered: boolean;
  onHover: (hovered: boolean) => void;
}) {
  const val = field.value as Record<string, unknown>;
  const fieldLabel = field.key.split(".").pop()?.toUpperCase() || field.key.toUpperCase();

  return (
    <div
      id={`field-card-${field.key}`}
      onClick={onSelect}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      className={cn(
        "border-b border-border/30 py-4 px-2 rounded-xl transition-all duration-150 cursor-pointer scroll-mt-2",
        isSelected && "bg-muted/40 ring-1 ring-violet-500/20",
        isBelowThreshold && "border-amber-500/30 bg-amber-500/[0.06] ring-1 ring-amber-500/20",
        isHovered && !isSelected && "bg-muted/10",
      )}
    >
      <div className="flex items-center justify-between mb-3 px-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-bold uppercase tracking-wider text-primary/90">
            {fieldLabel}
          </span>
          {field.required ? (
            <span className="rounded-md border border-violet-500/20 bg-violet-500/10 px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase text-violet-600 dark:text-violet-400">
              REQUIRED
            </span>
          ) : null}
          <FieldStatusLine field={field} isBelowThreshold={isBelowThreshold} />
        </div>
      </div>
      <div className="rounded-xl border border-border/60 bg-card/40 p-4 shadow-2xs backdrop-blur-sm space-y-4">
        {Object.entries(val).map(([k, v]) => (
          <ExtractionObjectValue key={k} label={k} value={v} />
        ))}
      </div>
      <div className="mt-3">
        <FieldEvidenceImages field={field} />
        <FieldEvidenceChips field={field} onSelect={onSelect} />
      </div>
      {isSelected ? (
        <div className="mt-3">
          <EditableJsonValue field={field} onValueChange={onValueChange} />
        </div>
      ) : null}
    </div>
  );
}

function ExtractionObjectValue({ label, value }: { label: string; value: unknown }) {
  const displayLabel = label.replace(/_/g, " ").toUpperCase();

  if (value === null || value === undefined) {
    return (
      <div className="flex items-baseline justify-between gap-4 py-2 border-b border-border/20 last:border-0">
        <span className="font-mono text-[11px] font-medium text-muted-foreground">{displayLabel}</span>
        <span className="text-sm text-muted-foreground">N/A</span>
      </div>
    );
  }

  // Array of primitives (e.g. related_entities, directors)
  if (Array.isArray(value)) {
    if (value.length === 0) return null;
    if (typeof value[0] !== "object") {
      return (
        <div className="py-2.5 border-b border-border/20 last:border-0 space-y-2">
          <span className="font-mono text-[11px] font-bold text-muted-foreground tracking-wider">{displayLabel}</span>
          <div className="flex flex-wrap gap-1.5 pt-0.5">
            {value.map((item, i) => (
              <div
                key={i}
                className="rounded-lg border border-border/60 bg-muted/50 px-2.5 py-1 text-xs font-medium text-foreground shadow-2xs hover:bg-muted/80 transition-colors"
              >
                <ValueWithImage value={item} />
              </div>
            ))}
          </div>
        </div>
      );
    }

    // Array of objects (e.g. major_shareholders, key_management)
    const rows = value as Record<string, unknown>[];
    const columns = Object.keys(rows[0] ?? {});
    return (
      <div className="py-2.5 border-b border-border/20 last:border-0 space-y-2.5">
        <span className="font-mono text-[11px] font-bold text-muted-foreground tracking-wider">{displayLabel}</span>
        <div className="overflow-hidden rounded-lg border border-border/60 bg-background/50 shadow-2xs">
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-border/60 bg-muted/50">
                  {columns.map((col) => (
                    <th key={col} className="px-3.5 py-2 font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                      {col.replace(/_/g, " ")}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/20">
                {rows.map((r, ri) => (
                  <tr key={ri} className="transition-colors hover:bg-muted/20">
                    {columns.map((col) => (
                      <td key={col} className="px-3.5 py-2.5 text-xs font-medium text-foreground">
                        <ValueWithImage value={r[col]} compact />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    );
  }

  // Nested object (e.g. statement_of_financial_position_RM_million, key_ratios_percent)
  if (typeof value === "object" && value !== null) {
    return (
      <div className="py-2.5 border-b border-border/20 last:border-0 space-y-2">
        <div className="font-mono text-xs font-bold uppercase tracking-wider text-primary/90 pb-1.5 border-b border-border/30 pt-1">
          {displayLabel}
        </div>
        <div className="pl-3 space-y-1">
          {Object.entries(value as Record<string, unknown>).map(([nk, nv]) => (
            <ExtractionObjectValue key={nk} label={nk} value={nv} />
          ))}
        </div>
      </div>
    );
  }

  // Primitive value
  return (
    <div className="flex items-baseline justify-between gap-4 py-2 border-b border-border/20 last:border-0">
      <span className="font-mono text-[11px] font-medium text-muted-foreground">{displayLabel}</span>
      <span className="text-sm font-semibold text-foreground text-right">
        <ValueWithImage value={value} />
      </span>
    </div>
  );
}

/**
 * Shadcn/Aceternity style table field: sleek container with glassmorphic header,
 * uppercase tracking, and clean row borders.
 */
function ExtractionTableField({
  field,
  isSelected,
  isBelowThreshold,
  onSelect,
  onValueChange,
  isHovered,
  onHover,
}: {
  field: ExtractionFieldResult;
  isSelected: boolean;
  isBelowThreshold: boolean;
  onSelect: () => void;
  onValueChange: (value: unknown) => void;
  isHovered: boolean;
  onHover: (hovered: boolean) => void;
}) {
  const value = field.value;
  if (!Array.isArray(value) || value.length === 0) {
    return (
      <ExtractionRowField
        field={field}
        isSelected={isSelected}
        isBelowThreshold={isBelowThreshold}
        onSelect={onSelect}
        onValueChange={onValueChange}
        isHovered={isHovered}
        onHover={onHover}
      />
    );
  }

  const rows = value as Record<string, unknown>[];
  const columns = Object.keys(rows[0] ?? {});
  const fieldLabel = field.key.split(".").pop()?.toUpperCase() || field.key.toUpperCase();

  return (
    <div
      id={`field-card-${field.key}`}
      onClick={onSelect}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      className={cn(
        "border-b border-border/30 py-4 px-2 rounded-xl transition-all duration-150 cursor-pointer scroll-mt-2",
        isSelected && "bg-muted/40 ring-1 ring-violet-500/20",
        isBelowThreshold && "border-amber-500/30 bg-amber-500/[0.06] ring-1 ring-amber-500/20",
        isHovered && !isSelected && "bg-muted/10",
      )}
    >
      <div className="flex items-center justify-between mb-3 px-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-bold uppercase tracking-wider text-primary/90">
            {fieldLabel}
          </span>
          {field.required ? (
            <span className="rounded-md border border-violet-500/20 bg-violet-500/10 px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase text-violet-600 dark:text-violet-400">
              REQUIRED
            </span>
          ) : null}
          <FieldStatusLine field={field} isBelowThreshold={isBelowThreshold} />
        </div>
      </div>
      <div className="overflow-hidden rounded-xl border border-border/60 bg-card/40 shadow-2xs backdrop-blur-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border/60 bg-muted/50">
                {columns.map((col) => (
                  <th
                    key={col}
                    className="whitespace-nowrap px-4 py-3 text-[11px] font-mono font-bold uppercase tracking-wider text-muted-foreground"
                  >
                    <div className="flex items-center gap-1.5">
                      {col.replace(/_/g, " ").toUpperCase()}
                      {col.toLowerCase() === "name" ? (
                        <span className="rounded-md border border-violet-500/20 bg-violet-500/10 px-1.5 py-0.5 text-[8px] font-mono font-bold uppercase text-violet-600 dark:text-violet-400">
                          REQUIRED
                        </span>
                      ) : null}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/30">
              {rows.map((row, rowIdx) => (
                <tr key={rowIdx} className="transition-colors hover:bg-muted/30">
                  {columns.map((col) => (
                    <td key={col} className="px-4 py-3 text-sm font-medium text-foreground/90">
                      <ValueWithImage value={row[col]} compact />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="mt-3">
        <FieldEvidenceImages field={field} />
        <FieldEvidenceChips field={field} onSelect={onSelect} />
      </div>
      {isSelected ? (
        <div className="mt-3">
          <EditableJsonValue field={field} onValueChange={onValueChange} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * Shadcn/Aceternity style list field: renders array of items as a grid of step cards
 * with numbered index badges, matching modern Aceternity card aesthetics.
 */
function ExtractionListField({
  field,
  isSelected,
  isBelowThreshold,
  onSelect,
  onValueChange,
  isHovered,
  onHover,
}: {
  field: ExtractionFieldResult;
  isSelected: boolean;
  isBelowThreshold: boolean;
  onSelect: () => void;
  onValueChange: (value: unknown) => void;
  isHovered: boolean;
  onHover: (hovered: boolean) => void;
}) {
  const items = Array.isArray(field.value) ? field.value : [];
  const fieldLabel = field.key.split(".").pop()?.toUpperCase() || field.key.toUpperCase();

  return (
    <div
      id={`field-card-${field.key}`}
      onClick={onSelect}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      className={cn(
        "border-b border-border/30 py-4 px-2 rounded-xl transition-all duration-150 cursor-pointer scroll-mt-2",
        isSelected && "bg-muted/40 ring-1 ring-violet-500/20",
        isBelowThreshold && "border-amber-500/30 bg-amber-500/[0.06] ring-1 ring-amber-500/20",
        isHovered && !isSelected && "bg-muted/10",
      )}
    >
      <div className="flex items-center justify-between mb-3 px-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-bold uppercase tracking-wider text-primary/90">
            {fieldLabel}
          </span>
          {field.required ? (
            <span className="rounded-md border border-violet-500/20 bg-violet-500/10 px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase text-violet-600 dark:text-violet-400">
              REQUIRED
            </span>
          ) : null}
          <FieldStatusLine field={field} isBelowThreshold={isBelowThreshold} />
        </div>
      </div>
      <div className="grid gap-2.5">
        {items.map((item, idx) => (
          <div
            key={idx}
            className="group flex items-start gap-3.5 rounded-xl border border-border/50 bg-card/40 p-3.5 text-sm leading-relaxed text-foreground shadow-2xs transition-all hover:border-border/80 hover:bg-card hover:shadow-sm"
          >
            <span className="flex size-6 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-muted/60 font-mono text-xs font-bold text-muted-foreground group-hover:bg-primary/10 group-hover:text-primary transition-colors">
              {idx + 1}
            </span>
            <span className="min-w-0 flex-1 pt-0.5 font-normal text-foreground/90">
              <ValueWithImage value={item} />
            </span>
          </div>
        ))}
      </div>
      <div className="mt-3">
        <FieldEvidenceImages field={field} />
        <FieldEvidenceChips field={field} onSelect={onSelect} />
      </div>
      {isSelected ? (
        <div className="mt-3">
          <EditableJsonValue field={field} onValueChange={onValueChange} />
        </div>
      ) : null}
    </div>
  );
}

function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    if (typeof value[0] === "object") return `${value.length} items`;
    return value.map((v) => formatFieldValue(v)).join(", ");
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function extractResultText(fields: ExtractionFieldResult[]): string {
  return fields
    .map((f) => `${f.key}: ${formatFieldValue(f.value)} (${Math.round(f.confidence * 100)}%)`)
    .join("\n");
}

function downloadResultJson(data: Record<string, unknown>, schemaName: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${schemaName.replace(/\s+/g, "_")}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function EvidenceDocumentViewer({
  input,
  chunks,
  fields,
  page,
  pageCount,
  zoom,
  selectedFieldKey,
  onPageChange,
  onZoomChange,
  onFieldSelect,
  hoveredFieldKey,
  onHoverField,
}: {
  input: ParserInputInfo;
  chunks: ExtractionChunk[];
  fields: ExtractionFieldResult[];
  page: number;
  pageCount: number;
  zoom: number;
  selectedFieldKey: string | null;
  onPageChange: (page: number) => void;
  onZoomChange: (zoom: number) => void;
  onFieldSelect: (key: string | null) => void;
  hoveredFieldKey: string | null;
  onHoverField: (key: string | null) => void;
}) {
  const [pageImageFailed, setPageImageFailed] = React.useState(false);
  const [rotated, setRotated] = React.useState(false);

  React.useEffect(() => {
    setPageImageFailed(false);
  }, [input.id, page]);

  React.useEffect(() => {
    if (selectedFieldKey) {
      const timer = setTimeout(() => {
        const element = document.getElementById("bbox-overlay-selected");
        if (element) {
          element.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [selectedFieldKey, page]);

  const boundedPage = Math.min(Math.max(page, 1), pageCount);
  const canRenderPage = input.input_type === "pdf" || input.input_type === "image";
  const pageImageUrl = parserBenchmarksApi.pageImageUrl(
    input.id,
    input.input_type === "image" ? 1 : boundedPage,
    1.6,
  );
  const previewUrl = parserBenchmarksApi.previewUrl(input.id);

  const pageChunks = chunks.filter((chunk) => chunk.page === boundedPage);
  const pageBounds = React.useMemo(() => {
    let maxX = 612;
    let maxY = 792;
    for (const chunk of chunks) {
      if (chunk.page !== boundedPage || !chunk.bbox) continue;
      const x1 = chunk.bbox.x1 ?? chunk.bbox.right ?? chunk.bbox.width ?? 0;
      const bottom = chunk.bbox.bottom ?? chunk.bbox.y1 ?? chunk.bbox.height ?? 0;
      if (Number(x1) > 50) maxX = Math.max(maxX, Number(x1));
      if (Number(bottom) > 50) maxY = Math.max(maxY, Number(bottom));
    }
    for (const field of fields) {
      for (const ev of field.evidence) {
        if (ev.page !== boundedPage || !ev.bbox) continue;
        const x1 = ev.bbox.x1 ?? ev.bbox.right ?? ev.bbox.width ?? 0;
        const bottom = ev.bbox.bottom ?? ev.bbox.y1 ?? ev.bbox.height ?? 0;
        if (Number(x1) > 50) maxX = Math.max(maxX, Number(x1));
        if (Number(bottom) > 50) maxY = Math.max(maxY, Number(bottom));
      }
    }
    return { width: maxX, height: maxY };
  }, [chunks, fields, boundedPage]);

  const highlightedChunkIds = React.useMemo(() => {
    if (!selectedFieldKey) return new Set<string>();
    const field = fields.find((f) => f.key === selectedFieldKey);
    if (!field) return new Set<string>();
    return new Set(field.evidence.map((e) => e.chunk_id));
  }, [selectedFieldKey, fields]);

  const hoveredChunkIds = React.useMemo(() => {
    if (!hoveredFieldKey) return new Set<string>();
    const field = fields.find((f) => f.key === hoveredFieldKey);
    if (!field) return new Set<string>();
    return new Set(field.evidence.map((e) => e.chunk_id));
  }, [hoveredFieldKey, fields]);

  const bboxOverlays = React.useMemo(() => {
    const overlays: Array<{
      id: string;
      chunkId: string;
      fieldKey: string | null;
      fieldLabel: string | null;
      fieldValue: string;
      leftPct: number;
      topPct: number;
      widthPct: number;
      heightPct: number;
      isHighlighted: boolean;
      isHovered: boolean;
    }> = [];

    const seenBoxes = new Set<string>();

    for (const field of fields) {
      for (const [idx, ev] of field.evidence.entries()) {
        if (ev.page !== boundedPage) continue;
        const chunk = chunks.find((c) => c.id === ev.chunk_id);
        const bbox = ev.bbox || chunk?.bbox;
        if (!bbox) continue;

        const x0 = Number(bbox.x0 ?? bbox.left ?? 0);
        const top = Number(bbox.top ?? bbox.y0 ?? 0);
        const x1 = Number(bbox.x1 ?? bbox.right ?? bbox.width ?? 0);
        const bottom = Number(bbox.bottom ?? bbox.y1 ?? bbox.height ?? 0);

        const leftPct = (x0 / pageBounds.width) * 100;
        const topPct = (top / pageBounds.height) * 100;
        const widthPct = ((x1 - x0) / pageBounds.width) * 100;
        const heightPct = ((bottom - top) / pageBounds.height) * 100;

        const isHighlighted = selectedFieldKey === field.key || highlightedChunkIds.has(ev.chunk_id);
        const isHovered = hoveredFieldKey === field.key || hoveredChunkIds.has(ev.chunk_id);

        overlays.push({
          id: `${field.key}-ev-${idx}`,
          chunkId: ev.chunk_id,
          fieldKey: field.key,
          fieldLabel: field.label || field.key,
          fieldValue: formatFieldValue(field.value),
          leftPct: Math.max(0, leftPct),
          topPct: Math.max(0, topPct),
          widthPct: Math.max(0.5, widthPct),
          heightPct: Math.max(0.5, heightPct),
          isHighlighted,
          isHovered,
        });
        seenBoxes.add(ev.chunk_id);
      }
    }

    for (const chunk of pageChunks) {
      if (!chunk.bbox || seenBoxes.has(chunk.id)) continue;
      const x0 = Number(chunk.bbox.x0 ?? chunk.bbox.left ?? 0);
      const top = Number(chunk.bbox.top ?? chunk.bbox.y0 ?? 0);
      const x1 = Number(chunk.bbox.x1 ?? chunk.bbox.right ?? chunk.bbox.width ?? 0);
      const bottom = Number(chunk.bbox.bottom ?? chunk.bbox.y1 ?? chunk.bbox.height ?? 0);
      const leftPct = (x0 / pageBounds.width) * 100;
      const topPct = (top / pageBounds.height) * 100;
      const widthPct = ((x1 - x0) / pageBounds.width) * 100;
      const heightPct = ((bottom - top) / pageBounds.height) * 100;

      const isHighlighted = highlightedChunkIds.has(chunk.id);
      const isHovered = hoveredChunkIds.has(chunk.id);
      const fieldUsingChunk = fields.find((f) => f.evidence.some((e) => e.chunk_id === chunk.id));

      overlays.push({
        id: `chunk-${chunk.id}`,
        chunkId: chunk.id,
        fieldKey: fieldUsingChunk?.key ?? null,
        fieldLabel: fieldUsingChunk?.label ?? fieldUsingChunk?.key ?? null,
        fieldValue: fieldUsingChunk ? formatFieldValue(fieldUsingChunk.value) : "",
        leftPct: Math.max(0, leftPct),
        topPct: Math.max(0, topPct),
        widthPct: Math.max(0.5, widthPct),
        heightPct: Math.max(0.5, heightPct),
        isHighlighted,
        isHovered,
      });
    }

    return overlays;
  }, [fields, boundedPage, pageChunks, pageBounds, selectedFieldKey, hoveredFieldKey, highlightedChunkIds, hoveredChunkIds]);

  function handleFullscreen() {
    window.open(previewUrl, "_blank", "noopener,noreferrer");
  }

  function handleDownload() {
    const anchor = document.createElement("a");
    anchor.href = previewUrl;
    anchor.download = input.name;
    anchor.click();
  }

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      {/* Gold-standard header: filename chip + pagination + zoom controls */}
      <div className="flex items-center gap-2 border-b border-border/70 bg-background px-3 py-2">
        {/* Filename chip */}
        <button
          type="button"
          className="flex items-center gap-1.5 rounded-md border border-border bg-muted/30 px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted/60 min-w-0 max-w-[200px]"
          onClick={() => navigator.clipboard.writeText(input.name)}
          title="Click to copy filename"
        >
          <span className="truncate">{input.name}</span>
          <Copy className="size-3 shrink-0 text-muted-foreground" />
        </button>
        {/* Page navigation */}
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          disabled={boundedPage <= 1}
          onClick={() => onPageChange(Math.max(1, boundedPage - 1))}
        >
          <ChevronLeft className="size-3.5" />
        </Button>
        <input
          value={boundedPage}
          onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isFinite(next)) onPageChange(Math.min(Math.max(next, 1), pageCount));
          }}
          className="h-7 w-10 rounded border border-border bg-background text-center text-xs font-medium tabular-nums"
        />
        <span className="text-xs text-muted-foreground">of {pageCount}</span>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          disabled={boundedPage >= pageCount}
          onClick={() => onPageChange(Math.min(pageCount, boundedPage + 1))}
        >
          <ChevronRight className="size-3.5" />
        </Button>
        <div className="mx-1 h-4 w-px bg-border" />
        {/* Zoom controls */}
        <Button variant="ghost" size="icon" className="size-7" onClick={() => onZoomChange(Math.max(50, zoom - 10))}>
          <ZoomOut className="size-3.5" />
        </Button>
        <span className="min-w-8 text-center text-xs font-medium tabular-nums">{zoom}%</span>
        <Button variant="ghost" size="icon" className="size-7" onClick={() => onZoomChange(Math.min(200, zoom + 10))}>
          <ZoomIn className="size-3.5" />
        </Button>
        {/* Extra controls pushed right */}
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="icon" className="size-7" onClick={() => { setRotated(!rotated); }}>
            <RotateCw className="size-3.5" />
          </Button>
          <Button variant="ghost" size="icon" className="size-7" onClick={handleDownload}>
            <Download className="size-3.5" />
          </Button>
          <Button variant="ghost" size="icon" className="size-7" onClick={handleFullscreen}>
            <Maximize2 className="size-3.5" />
          </Button>
        </div>
      </div>

      <div id="document-viewer-scroll-container" className="h-[780px] overflow-auto bg-[#f7f7f8] p-4">
        {canRenderPage && !pageImageFailed ? (
          <div
            className="relative mx-auto origin-top rounded-sm bg-white shadow-sm ring-1 ring-border"
            style={{
              width: `${Math.round(580 * (zoom / 100))}px`,
              transform: rotated ? "rotate(90deg) translateY(-100%)" : undefined,
              transformOrigin: "top left",
            }}
            onMouseLeave={() => onHoverField(null)}
          >
            <img
              src={pageImageUrl}
              alt={`${input.name} page ${boundedPage}`}
              className="block w-full select-none"
              draggable={false}
              onError={() => setPageImageFailed(true)}
            />
            {bboxOverlays.length > 0 ? (
              <div className="absolute inset-0">
                {bboxOverlays.map((overlay) => {
                  const showTooltip = overlay.isHighlighted || overlay.isHovered;
                  return (
                    <div
                      key={overlay.id}
                      className="absolute pointer-events-none"
                      style={{
                        left: `${overlay.leftPct}%`,
                        top: `${overlay.topPct}%`,
                        width: `${overlay.widthPct}%`,
                        height: `${overlay.heightPct}%`,
                      }}
                    >
                      <button
                        id={overlay.isHighlighted ? "bbox-overlay-selected" : undefined}
                        type="button"
                        className={cn(
                          "absolute inset-0 rounded-sm border transition-all pointer-events-auto",
                          overlay.isHighlighted
                            ? "border-violet-500 bg-violet-500/25 shadow-[0_0_0_2.5px_rgba(139,92,246,0.35)] z-20"
                            : overlay.isHovered
                            ? "border-violet-400 bg-violet-500/15 z-10"
                            : "border-transparent bg-transparent hover:border-violet-400 hover:bg-violet-500/10",
                        )}
                        onClick={() => onFieldSelect(overlay.fieldKey)}
                        onMouseEnter={() => onHoverField(overlay.fieldKey)}
                        onMouseLeave={() => onHoverField(null)}
                        title={overlay.fieldKey ?? "Evidence chunk"}
                      />
                      {showTooltip && overlay.fieldKey && (
                        <div
                          className="absolute z-30 flex flex-col items-center bg-slate-900 text-white rounded-md px-2.5 py-1.5 shadow-md border border-slate-800 text-[10px] font-sans font-medium whitespace-nowrap pointer-events-none select-none"
                          style={{
                            left: "50%",
                            top: "-6px",
                            transform: "translate(-50%, -100%)",
                          }}
                        >
                          <span className="font-semibold text-[9px] uppercase tracking-wide text-violet-300">
                            {overlay.fieldLabel?.toUpperCase()}
                          </span>
                          {overlay.fieldValue && (
                            <span className="mt-0.5 text-slate-100 max-w-[180px] truncate">
                              {overlay.fieldValue}
                            </span>
                          )}
                          <div className="absolute left-1/2 bottom-0 w-0 h-0 border-l-[5px] border-l-transparent border-r-[5px] border-r-transparent border-t-[5px] border-t-slate-900 -mb-[5px] -translate-x-1/2" />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : null}
            {bboxOverlays.length === 0 && pageChunks.length === 0 ? (
              <div className="pointer-events-none absolute left-4 top-4 rounded-md border border-border bg-background/90 px-2.5 py-1 text-xs text-muted-foreground shadow-sm">
                No evidence with bbox data on this page
              </div>
            ) : pageChunks.length > 0 && bboxOverlays.length === 0 ? (
              <div className="pointer-events-none absolute left-4 top-4 max-w-[360px] rounded-md border border-amber-200 bg-amber-50/95 px-2.5 py-1 text-xs leading-5 text-amber-900 shadow-sm">
                Evidence items on this page lack precise bounding box data.
              </div>
            ) : null}
          </div>
        ) : input.input_type === "pdf" ? (
          <div className="h-full overflow-hidden rounded-lg border border-border bg-background">
            <div className="border-b border-border bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
              Page image rendering unavailable. Install pymupdf for overlay highlights.
            </div>
            <iframe
              title={`${input.name} page ${boundedPage}`}
              src={`${previewUrl}#toolbar=1&navpanes=0&scrollbar=1&page=${boundedPage}`}
              className="h-[740px] w-full border-0"
            />
          </div>
        ) : (
          <EmptyState
            icon={<FileText className="size-5" />}
            title="Page preview not available"
            description="Only PDF and image inputs support visual evidence highlighting."
          />
        )}
      </div>
    </div>
  );
}

function UnifiedReport({ result }: { result: ExtractionRunResponse }) {
  const reportM = useMutation({
    mutationFn: () => extractionLabApi.report({ result }),
    onError: (error) => toast.error("Report generation failed", { description: String(error) }),
  });
  const fieldByKey = new Map(result.fields.map((field) => [field.key, field]));
  const fields = result.schema_definition.fields
    .map((schemaField) => fieldByKey.get(schemaField.key))
    .filter((field): field is ExtractionFieldResult => Boolean(field));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-muted/20 p-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold">OpenAI Report</p>
          <p className="text-xs text-muted-foreground">
            Builds a readable report from raw fields, validation status, and top evidence citations.
          </p>
        </div>
        <Button size="sm" onClick={() => reportM.mutate()} disabled={reportM.isPending}>
          {reportM.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />}
          Generate Report
        </Button>
      </div>

      {reportM.data?.report_markdown ? (
        <div className="rounded-lg border border-border bg-background p-4">
          <MarkdownReport markdown={reportM.data.report_markdown} />
        </div>
      ) : reportM.error ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200">
          {String(reportM.error)}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
          Generate a polished report to replace the raw field-by-field view.
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-4 xl:grid-cols-7">
        <div className="rounded-lg border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">Schema</p>
          <p className="truncate text-sm font-semibold">{result.schema_definition.name}</p>
        </div>
        <div className="rounded-lg border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">Parser</p>
          <p className="truncate text-sm font-semibold">{result.parser_name}</p>
        </div>
        <div className="rounded-lg border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">Tier</p>
          <p className="truncate text-sm font-semibold">{result.extraction_tier === "agentic" ? "Agentic" : "Cost effective"}</p>
        </div>
        <div className="rounded-lg border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">Retrieval</p>
          <p className="truncate text-sm font-semibold">
            {result.stats.retrieval_mode === "full_pipeline"
              ? "Hybrid RRF"
              : result.stats.retrieval_mode === "dense_only"
              ? "Dense Only"
              : result.stats.retrieval_mode === "bm25_only"
              ? "BM25 Only"
              : result.stats.retrieval_mode === "fts_fallback"
              ? "FTS Fallback"
              : result.stats.retrieval_mode === "in_memory"
              ? "In-Memory"
              : result.stats.retrieval_mode}
          </p>
          <p className="text-[10px] text-muted-foreground mt-0.5 truncate">
            {result.stats.dense_hits} dense / {result.stats.sparse_hits} BM25
          </p>
        </div>
        <div className="rounded-lg border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">Validated</p>
          <p className="text-sm font-semibold">
            {fields.filter((field) => field.valid).length} / {fields.length}
          </p>
        </div>
        <div className="rounded-lg border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">Consistency</p>
          <p className="text-sm font-semibold">{Math.round(result.stats.consistency_score * 100)}%</p>
        </div>
        <div className="rounded-lg border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">Agentic Checks</p>
          <p className="text-sm font-semibold">
            {result.stats.null_retries} retry / {result.stats.candidate_conflicts} conflict / {result.stats.critic_issues} critic
          </p>
        </div>
      </div>

      <div className="overflow-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Field Coverage</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Confidence</TableHead>
              <TableHead>Primary Citation</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {fields.map((field) => (
              <TableRow key={field.key}>
                <TableCell>
                  <p className="font-medium">{field.label || field.key}</p>
                  <p className="font-mono text-xs text-muted-foreground">{field.key}</p>
                </TableCell>
                <TableCell>
                  <Badge tone={field.valid ? "emerald" : "rose"}>{field.valid ? "valid" : "invalid"}</Badge>
                </TableCell>
                <TableCell>{Math.round(field.confidence * 100)}%</TableCell>
                <TableCell className="max-w-[360px] truncate text-xs text-muted-foreground">
                  {field.evidence[0]
                    ? `Page ${field.evidence[0].page} / ${field.evidence[0].type} / ${field.evidence[0].chunk_id}`
                    : "No citation"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function MarkdownReport({ markdown }: { markdown: string }) {
  return (
    <div className="space-y-2 text-sm leading-6">
      {markdown.split(/\n{2,}/).map((block, index) => {
        const clean = block.trim();
        if (!clean) return null;
        if (clean.startsWith("### ")) {
          return <h4 key={index} className="pt-2 text-sm font-semibold">{clean.slice(4)}</h4>;
        }
        if (clean.startsWith("## ")) {
          return <h3 key={index} className="pt-2 text-base font-semibold">{clean.slice(3)}</h3>;
        }
        if (clean.startsWith("# ")) {
          return <h2 key={index} className="text-lg font-semibold">{clean.slice(2)}</h2>;
        }
        if (/^[-*]\s/m.test(clean)) {
          return (
            <ul key={index} className="list-disc space-y-1 pl-5">
              {clean.split("\n").map((line, lineIndex) => (
                <li key={lineIndex}>{line.replace(/^[-*]\s*/, "")}</li>
              ))}
            </ul>
          );
        }
        return <p key={index} className="whitespace-pre-wrap">{clean}</p>;
      })}
    </div>
  );
}

function ExcelResult({ result }: { result: ExtractionRunResponse }) {
  const rows = excelRows(result);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <Badge tone="teal">{rows.length} schema rows</Badge>
          <Badge tone="slate">{result.schema_definition.name}</Badge>
        </div>
        <Button size="sm" variant="outline" onClick={() => downloadExcel(result)}>
          <Download className="size-3.5" />
          Download .xls
        </Button>
      </div>
      <div className="overflow-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              {["Field", "Key", "Type", "Value", "Confidence", "Valid", "Evidence"].map((column) => (
                <TableHead key={column} className="whitespace-nowrap">{column}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.key}>
                <TableCell className="font-medium">{row.field}</TableCell>
                <TableCell className="font-mono text-xs">{row.key}</TableCell>
                <TableCell>{row.type}</TableCell>
                <TableCell className="max-w-[420px] whitespace-pre-wrap">{row.value}</TableCell>
                <TableCell>{row.confidence}</TableCell>
                <TableCell>{row.valid}</TableCell>
                <TableCell className="max-w-[420px] whitespace-pre-wrap">{row.evidence}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function excelRows(result: ExtractionRunResponse) {
  const fieldByKey = new Map(result.fields.map((field) => [field.key, field]));
  return result.schema_definition.fields.map((schemaField) => {
    const field = fieldByKey.get(schemaField.key);
    const evidence = field?.evidence?.[0];
    return {
      field: schemaField.label || schemaField.key,
      key: schemaField.key,
      type: schemaField.type,
      value: valueToCell(field?.value),
      confidence: field ? `${Math.round(field.confidence * 100)}%` : "",
      valid: field ? (field.valid ? "Yes" : "No") : "No",
      evidence: evidence ? `Page ${evidence.page}: ${evidence.text_preview}` : "",
    };
  });
}

function fullResultPayload(result: ExtractionRunResponse) {
  return {
    run_id: result.run_id,
    input: result.input,
    parser_id: result.parser_id,
    parser_name: result.parser_name,
    parser_run_id: result.parser_run_id,
    parser_run_started_at: result.parser_run_started_at,
    extraction_tier: result.extraction_tier,
    schema_model_name: result.schema_model_name,
    schema_definition: result.schema_definition,
    natural_language_query: result.natural_language_query,
    data: result.data,
    fields: result.fields,
    chunks: result.chunks,
    validation_errors: result.validation_errors,
    warnings: result.warnings,
    generated_code: result.generated_code,
    stats: result.stats,
    started_at: result.started_at,
    finished_at: result.finished_at,
  };
}

function downloadExcel(result: ExtractionRunResponse) {
  const rows = excelRows(result);
  const html = `<!doctype html><html><head><meta charset="utf-8" /></head><body><table border="1"><thead><tr>${[
    "Field",
    "Key",
    "Type",
    "Value",
    "Confidence",
    "Valid",
    "Evidence",
  ].map((cell) => `<th>${escapeHtml(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${[
    row.field,
    row.key,
    row.type,
    row.value,
    row.confidence,
    row.valid,
    row.evidence,
  ].map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></body></html>`;
  const blob = new Blob([html], { type: "application/vnd.ms-excel;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${slugify(result.schema_definition.name)}-${result.run_id}.xls`;
  link.click();
  URL.revokeObjectURL(url);
}

function valueToCell(value: unknown): string {
  if (value == null || value === "") return "";
  if (Array.isArray(value) || typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function renderReadableValue(value: unknown): React.ReactNode {
  if (value == null || value === "") return <p className="text-sm text-muted-foreground">Not extracted</p>;
  if (Array.isArray(value) || typeof value === "object") return renderValue(value);
  return <p className="whitespace-pre-wrap text-base leading-7">{String(value)}</p>;
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function EvidenceTable({ rows }: { rows: Record<string, string>[] }) {
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 12);
  return (
    <div className="max-h-72 overflow-auto rounded-md border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((column) => (
              <TableHead key={column} className="whitespace-nowrap font-mono text-xs">
                {column}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.slice(0, 40).map((row, rowIndex) => (
            <TableRow key={rowIndex}>
              {columns.map((column) => (
                <TableCell key={column} className="max-w-[260px] truncate text-xs">
                  {row[column] ?? ""}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function ExtractedField({ field }: { field: ExtractionFieldResult }) {
  const confidenceTone = field.confidence >= 0.85 ? "emerald" : field.confidence >= 0.65 ? "amber" : "rose";
  return (
    <div className="rounded-lg border border-border bg-background p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-semibold">{field.label || field.key}</p>
            {field.required ? <Badge tone="violet">Required</Badge> : null}
          </div>
          <p className="font-mono text-xs text-muted-foreground">{field.key}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={field.valid ? "emerald" : "rose"}>{field.valid ? "Valid" : "Invalid"}</Badge>
          <Badge tone={confidenceTone}>{Math.round(field.confidence * 100)}%</Badge>
        </div>
      </div>
      {renderValue(field.value)}
      {field.validation_message ? (
        <p className="mt-2 text-xs text-destructive">{field.validation_message}</p>
      ) : null}
      {field.evidence.length > 0 ? (
        <div className="mt-3 border-t border-border/70 pt-3">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Evidence</p>
          <FieldEvidenceImages field={field} />
          {field.evidence.slice(0, 2).map((item) => (
            <p key={item.chunk_id} className="text-xs leading-5 text-muted-foreground">
              Page {item.page}: {item.text_preview}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function CodePanel({
  schema,
  result,
}: {
  schema: ExtractionLabSchema;
  result: ExtractionRunResponse | null;
}) {
  const code = result?.generated_code ?? generatedCodeForSchema(schema);
  const payload = {
    input_id: "<selected-input-id>",
    output_schema: schema,
    parser_id: "auto",
    chunking_strategy: "page-by-page",
    max_pages: 20,
    max_candidates_per_field: 8,
    preview_chars: 8000,
    extraction_tier: "cost_effective",
  };
  return (
    <div className="space-y-4">
      <SectionCard title="Pydantic Model" description={result ? result.schema_model_name : schema.name}>
        <ScrollArea className="h-[420px] rounded-lg border border-border bg-muted/20">
          <pre className="whitespace-pre-wrap p-4 font-mono text-xs leading-5">{code}</pre>
        </ScrollArea>
      </SectionCard>
      <SectionCard title="Run Payload" description="API request body">
        <ScrollArea className="h-[360px] rounded-lg border border-border bg-muted/20">
          <pre className="whitespace-pre-wrap p-4 font-mono text-xs leading-5">
            {JSON.stringify(payload, null, 2)}
          </pre>
        </ScrollArea>
      </SectionCard>
    </div>
  );
}

function ProcessingPanel({
  input,
  parserId,
}: {
  input: ParserInputInfo | undefined;
  parserId: string;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-primary/20 bg-background ring-1 ring-inset ring-primary/10">
      <div className="relative px-5 py-4">
        <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-teal-500 via-amber-400 to-primary opacity-80" />
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Loader2 className="size-5 animate-spin" />
            </div>
            <div className="min-w-0 space-y-1">
              <h2 className="text-sm font-semibold tracking-tight">Running schema extraction</h2>
              <p className="truncate text-sm text-muted-foreground">
                {input?.name ?? "Selected document"} / {parserId === "auto" ? "auto local parser" : parserId}
              </p>
            </div>
          </div>
          <Badge tone="teal">Chunked</Badge>
        </div>
      </div>
    </section>
  );
}

function renderValue(value: unknown): React.ReactNode {
  if (value == null || value === "") {
    return <p className="text-sm text-muted-foreground">N/A</p>;
  }
  const images = imageRefsFromValue(value);
  if (images.length > 0) {
    return <ImagePreviewGrid images={images} />;
  }
  if (Array.isArray(value)) {
    if (value.length > 0 && value.every((item) => item && typeof item === "object" && !Array.isArray(item))) {
      const rows = value as Record<string, unknown>[];
      const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 12);
      return (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                {columns.map((column) => (
                  <TableHead key={column} className="whitespace-nowrap font-mono text-xs">
                    {column}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.slice(0, 30).map((row, rowIndex) => (
                <TableRow key={rowIndex}>
                  {columns.map((column) => (
                    <TableCell key={column} className="max-w-[260px] text-sm">
                      <ValueWithImage value={row[column]} compact />
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      );
    }
    return (
      <ul className="list-inside list-disc space-y-1 text-sm">
        {value.map((item, index) => (
          <li key={index}>
            <ValueWithImage value={item} />
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === "object") {
    return (
      <dl className="grid gap-2 sm:grid-cols-2">
        {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
          <div key={key} className="rounded-md border border-border bg-muted/20 px-3 py-2">
            <dt className="font-mono text-xs text-muted-foreground">{key}</dt>
            <dd className="mt-1 text-sm">
              <ValueWithImage value={item} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <p className="whitespace-pre-wrap text-sm leading-6">{String(value)}</p>;
}

function normalizeSchema(value: unknown): ExtractionLabSchema {
  if (!value || typeof value !== "object") {
    throw new Error("Schema must be an object.");
  }
  const raw = value as Record<string, unknown>;
  if (!Array.isArray(raw.fields)) {
    throw new Error("Schema must include a fields array.");
  }
  return {
    name: String(raw.name || "ExtractionResult"),
    description: raw.description == null ? null : String(raw.description),
    fields: raw.fields.map((field, index) => normalizeField(field, index)),
  };
}

function schemaFromNaturalLanguage(query: string): ExtractionLabSchema {
  const normalized = query.toLowerCase();
  const wantsFinancialTables = /\b(balance sheet|financial statement|financial statements|income statement|cash flow|equity|assets|liabilities)\b/.test(normalized);
  const wantsImages = /\b(image|images|figure|figures|chart|charts|visual)\b/.test(normalized);
  const name = wantsFinancialTables ? "FinancialStatementEvidence" : "NaturalLanguageExtraction";
  const fields: ExtractionSchemaField[] = [
    makeField(
      wantsFinancialTables ? "financial_statement_tables" : "relevant_tables",
      wantsFinancialTables ? "Financial Statement Tables" : "Relevant Tables",
      "list",
      false,
      wantsFinancialTables
        ? "All relevant balance sheet, financial statement, assets, liabilities, equity, income, cash flow, or accounting tables from parser outputs."
        : "All relevant tables from parser outputs for the user request.",
    ),
    makeField(
      "supporting_text",
      "Supporting Text",
      "list",
      false,
      "Relevant text snippets and explanations from parser outputs for the user request.",
    ),
    makeField(
      "answer_summary",
      "Answer Summary",
      "text",
      false,
      "Short answer grounded in extracted parser evidence.",
    ),
  ];
  if (wantsImages) {
    fields.splice(
      1,
      0,
      makeField(
        "relevant_images",
        "Relevant Images",
        "list",
        false,
        "Relevant images, figures, charts, and visual evidence links from parser outputs.",
      ),
    );
  }
  return {
    name,
    description: query,
    fields,
  };
}

function normalizeField(value: unknown, index: number): ExtractionSchemaField {
  if (!value || typeof value !== "object") {
    throw new Error(`Field ${index + 1} must be an object.`);
  }
  const raw = value as Record<string, unknown>;
  const type = normalizeBuilderFieldType(raw.type);
  const label = String(raw.label || raw.key || `Field ${index + 1}`);
  return {
    id: String(raw.id || uid()),
    key: slugify(String(raw.key || label)),
    label,
    type,
    description: raw.description == null ? null : String(raw.description),
    required: raw.required === true,
    children: Array.isArray(raw.children)
      ? raw.children.map((child, childIndex) => normalizeField(child, childIndex))
      : [],
  };
}

function normalizeBuilderFieldType(value: unknown): ExtractionFieldType {
  const rawType = String(value || "text").toLowerCase();
  if (rawType === "number" || rawType === "num" || rawType === "currency") return "number";
  if (rawType === "boolean" || rawType === "bool") return "boolean";
  if (rawType === "object" || rawType === "obj") return "object";
  if (rawType === "list" || rawType === "array" || rawType === "table") return "list";
  return "text";
}

function generatedCodeForSchema(schema: ExtractionLabSchema): string {
  const modelName = schema.name.replace(/[^a-zA-Z0-9]/g, "") || "ExtractionResult";
  const lines = [
    "from datetime import date",
    "from typing import Any",
    "",
    "from pydantic import BaseModel, Field",
    "",
    "",
    `class ${modelName}(BaseModel):`,
  ];
  if (schema.fields.length === 0) {
    lines.push("    pass");
    return lines.join("\n");
  }
  for (const field of schema.fields) {
    lines.push(
      `    ${field.key}: ${pythonType(field.type)} | None = Field(default=None, description="${escapePython(field.description || field.label || field.key)}")`,
    );
  }
  return lines.join("\n");
}

function pythonType(type: ExtractionFieldType) {
  if (type === "number") return "int | float";
  if (type === "currency") return "float";
  if (type === "date") return "date";
  if (type === "boolean") return "bool";
  if (type === "list") return "list[str]";
  if (type === "table") return "list[dict[str, Any]]";
  if (type === "object") return "dict[str, Any]";
  return "str";
}

function escapePython(value: string) {
  return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

interface HistoryTabProps {
  onLoadResult: (runId: string) => Promise<void>;
  onDeleteResult: (runId: string) => void;
}

function formatHistoryCost(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (value <= 0) {
    return "$0.0000";
  }
  if (value < 0.01) {
    return `$${value.toFixed(4)}`;
  }
  return `$${value.toFixed(2)}`;
}

function HistoryTab({ onLoadResult, onDeleteResult }: HistoryTabProps) {
  const [searchQuery, setSearchQuery] = React.useState("");
  const [isV2, setIsV2] = React.useState(true);

  const historyQ = useQuery({
    queryKey: ["extraction-lab-history"],
    queryFn: () => extractionLabApi.history(),
    refetchInterval: 3000,
  });

  const jobs = historyQ.data ?? [];

  const filteredJobs = React.useMemo(() => {
    return jobs.filter((job) => {
      const q = searchQuery.toLowerCase().trim();
      if (!q) return true;
      return job.job_id.toLowerCase().includes(q) || job.filename.toLowerCase().includes(q);
    });
  }, [jobs, searchQuery]);

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success("Copied to clipboard", { description: text });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border/50 bg-card p-4 shadow-sm">
        <div className="flex flex-1 min-w-[280px] max-w-md items-center gap-2">
          <div className="relative w-full">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Job ID (exact match)"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9 h-9 w-full bg-background"
            />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="h-9 gap-1.5 text-muted-foreground">
            <Calendar className="size-4" />
            <span>Pick date</span>
          </Button>
          <span className="text-muted-foreground/40 font-light">-</span>
          <Button variant="outline" size="sm" className="h-9 gap-1.5 text-muted-foreground">
            <Calendar className="size-4" />
            <span>Pick date</span>
          </Button>

          <Button variant="outline" size="sm" className="h-9 text-muted-foreground">
            Columns
          </Button>

          <Button variant="outline" size="sm" className="h-9 text-muted-foreground">
            Local time
          </Button>

          <div className="flex items-center gap-2 pl-2 border-l border-border/80">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">V2</span>
            <Switch
              checked={isV2}
              onCheckedChange={setIsV2}
              aria-label="V2 Mode Toggle"
            />
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-border/50 bg-card shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent bg-muted/30">
                <TableHead className="py-3.5 font-semibold text-foreground">Name</TableHead>
                <TableHead className="py-3.5 font-semibold text-foreground">Status</TableHead>
                <TableHead className="py-3.5 font-semibold text-foreground">Tier</TableHead>
                <TableHead className="py-3.5 font-semibold text-foreground">Queue</TableHead>
                <TableHead className="py-3.5 font-semibold text-foreground">Processing</TableHead>
                <TableHead className="py-3.5 font-semibold text-foreground">Total</TableHead>
                <TableHead className="py-3.5 font-semibold text-foreground">Cost</TableHead>
                <TableHead className="py-3.5 font-semibold text-foreground">ID</TableHead>
                <TableHead className="py-3.5 font-semibold text-foreground">Created</TableHead>
                <TableHead className="py-3.5 font-semibold text-foreground text-center">Results</TableHead>
                <TableHead className="py-3.5 w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {historyQ.isLoading ? (
                <TableRow>
                  <TableCell colSpan={11} className="h-32 text-center text-muted-foreground">
                    <div className="flex flex-col items-center justify-center gap-2">
                      <div className="size-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                      <span>Loading extraction history...</span>
                    </div>
                  </TableCell>
                </TableRow>
              ) : filteredJobs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={11} className="h-32 text-center text-muted-foreground">
                    No extraction jobs found.
                  </TableCell>
                </TableRow>
              ) : (
                filteredJobs.map((job) => {
                  const running = job.status === "RUNNING";
                  const failed = job.status === "FAILED";
                  const success = job.status === "SUCCESS";

                  return (
                    <TableRow key={job.job_id} className="hover:bg-muted/30">
                      <TableCell className="py-3.5 font-semibold text-foreground max-w-xs truncate">
                        {job.filename}
                      </TableCell>
                      <TableCell className="py-3.5">
                        <span className="inline-flex items-center gap-1.5">
                          <span
                            className={cn(
                              "size-2 rounded-full",
                              success && "bg-emerald-500 animate-pulse",
                              running && "bg-blue-500 animate-spin border border-t-transparent border-blue-200",
                              failed && "bg-rose-500"
                            )}
                          />
                          <span
                            className={cn(
                              "text-xs font-semibold tracking-wide uppercase",
                              success && "text-emerald-600 dark:text-emerald-400",
                              running && "text-blue-600 dark:text-blue-400",
                              failed && "text-rose-600 dark:text-rose-400"
                            )}
                          >
                            {job.status}
                          </span>
                        </span>
                      </TableCell>
                      <TableCell className="py-3.5 font-medium">
                        <span
                          className={cn(
                            "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                            job.tier === "Agentic"
                              ? "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300"
                              : "bg-slate-100 text-slate-800 dark:bg-slate-900/40 dark:text-slate-300"
                          )}
                        >
                          {job.tier}
                        </span>
                      </TableCell>
                      <TableCell className="py-3.5 text-muted-foreground font-mono text-xs tabular-nums">
                        {job.queue_time}
                      </TableCell>
                      <TableCell className="py-3.5 text-muted-foreground font-mono text-xs tabular-nums">
                        {running ? "-" : job.processing_time}
                      </TableCell>
                      <TableCell className="py-3.5 text-muted-foreground font-mono text-xs tabular-nums">
                        {running ? "-" : job.total_time}
                      </TableCell>
                      <TableCell className="py-3.5 text-muted-foreground font-mono text-xs tabular-nums">
                        {formatHistoryCost(job.estimated_cost_usd)}
                      </TableCell>
                      <TableCell className="py-3.5 font-mono text-xs text-muted-foreground">
                        <div className="flex items-center gap-1">
                          <span className="truncate max-w-[80px]">{job.job_id}</span>
                          <button
                            onClick={() => copyToClipboard(job.job_id)}
                            className="p-1 hover:bg-muted rounded text-muted-foreground hover:text-foreground transition-colors"
                            title="Copy Job ID"
                          >
                            <Copy className="size-3" />
                          </button>
                        </div>
                      </TableCell>
                      <TableCell className="py-3.5 text-muted-foreground text-xs font-medium">
                        {job.created_at}
                      </TableCell>
                      <TableCell className="py-3.5 text-center">
                        {job.result_run_id ? (
                          <Button
                            variant="link"
                            size="sm"
                            onClick={() => onLoadResult(job.job_id)}
                            className="h-auto p-0 font-semibold text-primary hover:underline"
                          >
                            Results
                          </Button>
                        ) : (
                          <span className="text-xs text-muted-foreground italic">
                            {running ? "Extracting..." : "No results"}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="py-3.5 text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="size-8">
                              <MoreVertical className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-36">
                            {job.result_run_id && (
                              <DropdownMenuItem
                                onClick={() => onLoadResult(job.job_id)}
                                className="cursor-pointer gap-2"
                              >
                                View Results
                              </DropdownMenuItem>
                            )}
                            <DropdownMenuItem
                              onClick={() => {
                                if (confirm(`Delete extraction job ${job.job_id}? This will permanently remove the job and its results.`)) {
                                  onDeleteResult(job.job_id);
                                }
                              }}
                              className="cursor-pointer text-destructive focus:text-destructive gap-2 focus:bg-destructive/10"
                            >
                              <Trash2 className="size-3.5" />
                              Delete Job
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  );
}
