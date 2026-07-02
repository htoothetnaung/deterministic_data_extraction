import fs from "fs";
import path from "path";

const NOTEBOOKS_DIR = path.resolve(process.cwd(), "notebooks");

export interface NotebookCell {
  id?: string;
  type: "markdown" | "code";
  source: string;
  outputs?: string[];
  execution_count?: number | null;
}

export interface Notebook {
  name: string;
  path: string;
  cells: NotebookCell[];
  metadata: Record<string, unknown>;
  nbformat: number;
  nbformat_minor: number;
}

function parseSource(source: string[] | string): string {
  if (Array.isArray(source)) return source.join("");
  return source;
}

function parseOutputs(outputs: unknown[]): string[] {
  return outputs.map((out) => {
    const record = out as Record<string, unknown>;
    const text = record.text;
    if (Array.isArray(text)) return text.join("");
    if (typeof text === "string") return text;
    const traceback = record.traceback;
    if (Array.isArray(traceback)) return traceback.join("\n");
    return JSON.stringify(out);
  });
}

export function listNotebooks(): string[] {
  if (!fs.existsSync(NOTEBOOKS_DIR)) return [];
  return fs
    .readdirSync(NOTEBOOKS_DIR)
    .filter((f) => f.endsWith(".ipynb"))
    .sort();
}

export function readNotebook(filename: string): Notebook {
  const filePath = path.join(NOTEBOOKS_DIR, filename);
  if (!fs.existsSync(filePath)) {
    throw new Error(`Notebook not found: ${filename}`);
  }

  const raw = JSON.parse(fs.readFileSync(filePath, "utf-8"));

  const cells: NotebookCell[] = (raw.cells || []).map(
    (cell: Record<string, unknown>) => ({
      id: (cell.id as string) ?? undefined,
      type: cell.cell_type as "markdown" | "code",
      source: parseSource(cell.source as string[] | string),
      outputs:
        cell.cell_type === "code"
          ? parseOutputs((cell.outputs as unknown[]) || [])
          : undefined,
      execution_count: (cell.execution_count as number | null) ?? null,
    })
  );

  return {
    name: filename,
    path: filePath,
    cells,
    metadata: raw.metadata as Record<string, unknown>,
    nbformat: raw.nbformat as number,
    nbformat_minor: raw.nbformat_minor as number,
  };
}

export function notebookToMarkdown(notebook: Notebook): string {
  const lines: string[] = [];

  for (const cell of notebook.cells) {
    if (cell.type === "markdown") {
      lines.push(cell.source);
      lines.push("");
    } else {
      lines.push("```python");
      lines.push(cell.source);
      lines.push("```");
      lines.push("");
      if (cell.outputs && cell.outputs.length > 0) {
        lines.push("```");
        lines.push(...cell.outputs);
        lines.push("```");
        lines.push("");
      }
    }
  }

  return lines.join("\n");
}
