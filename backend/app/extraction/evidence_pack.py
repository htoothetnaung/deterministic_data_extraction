from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.extraction.context_budget import ContextBudget


@dataclass
class EvidencePack:
    field_path: str
    query: str
    text_snippets: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    estimated_text_tokens: int = 0
    retrieval_reason: str = "weighted_fts_vector"

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def evidence_ids(self) -> list[str]:
        return [str(item["evidence_id"]) for item in [*self.tables, *self.text_snippets] if item.get("evidence_id")]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def build_evidence_pack(field_path: str, query: str, rows: list[dict[str, Any]], budget: ContextBudget) -> EvidencePack:
    pack = EvidencePack(field_path=field_path, query=query)
    table_count = 0
    for row in rows:
        content = str(row.get("markdown") or row.get("text") or "")
        if not content.strip():
            continue
        tokens = estimate_tokens(content)
        if len(pack.text_snippets) + len(pack.tables) >= budget.max_evidence_items:
            break
        if pack.estimated_text_tokens + tokens > budget.max_text_tokens:
            remaining = budget.max_text_tokens - pack.estimated_text_tokens
            if remaining <= 0:
                continue
            # Do not drop the only relevant evidence just because it is a
            # large document/page chunk. Keep a bounded prefix so extraction
            # still has context and citations instead of producing all-null
            # fields.
            content = content[: max(remaining * 4, 1)]
            tokens = estimate_tokens(content)
        item = dict(row)
        if row.get("markdown"):
            item["markdown"] = content
        else:
            item["text"] = content
        item["estimated_tokens"] = tokens
        if str(row.get("source_type", "")).startswith("table") and table_count < budget.max_tables:
            pack.tables.append(item)
            table_count += 1
        else:
            pack.text_snippets.append(item)
        pack.estimated_text_tokens += tokens
    return pack
