"""Context budget managers for structured field extraction.

Establishes token, table, and evidence limits dynamically depending on field types,
ensuring LLM prompts remain within hardware constraints and rate limits.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextBudget:
    """Configures retrieval thresholds and token limits for RAG contexts."""
    max_text_tokens: int = 8000
    max_evidence_items: int = 8
    max_tables: int = 5
    max_images: int = 0


def budget_for_field(field_schema: dict) -> ContextBudget:
    """Calculate the target context budget configuration based on field type and description features.

    * Financial metrics (numbers or description matching revenue/income) prioritize tables and compact text.
    * Complex structures (arrays or nested objects) request larger token budgets and extra evidence counts.
    * Text strings receive standard mid-tier budgets.
    """
    field_type = str(field_schema.get("type") or "string")
    description = str(field_schema.get("description") or "").lower()
    if field_type in {"number", "integer"} or "revenue" in description or "income" in description:
        return ContextBudget(max_text_tokens=4000, max_evidence_items=8, max_tables=5)
    if field_type in {"array", "object"}:
        return ContextBudget(max_text_tokens=12000, max_evidence_items=14, max_tables=6)
    return ContextBudget(max_text_tokens=6000, max_evidence_items=8, max_tables=3)
