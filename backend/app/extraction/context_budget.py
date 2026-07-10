"""Context budget managers for structured field extraction.

Establishes token, table, and evidence limits dynamically depending on field types,
ensuring LLM prompts remain within hardware constraints and rate limits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.settings import RuntimeSettings


@dataclass(frozen=True)
class ContextBudget:
    """Configures retrieval thresholds and token limits for RAG contexts."""
    max_text_tokens: int = 8000
    max_evidence_items: int = 8
    max_tables: int = 5
    max_images: int = 0


def budget_for_field(field_schema: dict, settings: RuntimeSettings | None = None) -> ContextBudget:
    """Calculate the target context budget configuration based on field type, description features, and runtime settings.

    * Financial metrics (numbers or description matching revenue/income) prioritize tables and compact text.
    * Complex structures (array or objects) request larger token budgets and extra evidence counts.
    * Text strings receive standard mid-tier budgets.
    * Dynamic limits (scalar_chunk_limit, narrative_chunk_limit, max_chunk_limit) are applied if settings are provided.
    """
    field_type = str(field_schema.get("type") or "string")
    description = str(field_schema.get("description") or "").lower()

    # Determine base values
    if field_type in {"number", "integer"} or "revenue" in description or "income" in description:
        base_tokens = 4000
        base_items = 8
        base_tables = 5
    elif field_type in {"array", "object"}:
        base_tokens = 12000
        base_items = 14
        base_tables = 6
    else:
        base_tokens = 6000
        base_items = 8
        base_tables = 3

    # Override with settings parameters if available
    if settings is not None:
        is_scalar = field_type in {"number", "integer", "boolean"}
        if is_scalar:
            base_items = settings.retrieval.scalar_chunk_limit
        else:
            base_items = settings.retrieval.narrative_chunk_limit
        # Cap by maximum chunk limit
        base_items = min(base_items, settings.retrieval.max_chunk_limit)

    return ContextBudget(
        max_text_tokens=base_tokens,
        max_evidence_items=base_items,
        max_tables=base_tables,
    )
