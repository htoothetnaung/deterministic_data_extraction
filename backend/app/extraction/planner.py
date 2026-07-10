"""Planner for field retrieval and context limits.

Parses target schema fields and associated hint inputs to generate structured
retrieval plans (determining preferred formats, token limits, and query details).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.extraction.context_budget import ContextBudget, budget_for_field

if TYPE_CHECKING:
    from app.models.settings import RuntimeSettings


@dataclass(frozen=True)
class FieldRetrievalPlan:
    """A generated plan to guide database RAG queries for a single target field."""
    field_path: str
    query: str
    expected_type: str
    keywords: list[str] = field(default_factory=list)
    preferred_source_types: list[str] = field(default_factory=list)
    metadata_filters: dict[str, str] = field(default_factory=dict)
    budget: ContextBudget = field(default_factory=ContextBudget)


class FieldRetrievalPlanner:
    """Generates extraction search plans using schema structures and field hints."""

    def plan(
        self,
        field_path: str,
        field_schema: dict,
        hints: dict | None = None,
        settings: RuntimeSettings | None = None,
    ) -> FieldRetrievalPlan:
        """Construct a query plan for a single field path.

        Automatically routes financial/numerical metrics to prefer table structures
        and configures context budgets.
        """
        hints = hints or {}
        description = str(field_schema.get("description") or hints.get("description") or "")
        expected_type = str(field_schema.get("type") or hints.get("value_type") or "string")
        keywords = list(dict.fromkeys([*_tokens(field_path), *_tokens(description), *hints.get("keywords", [])]))
        preferred = list(hints.get("preferred_source_types") or [])
        if expected_type in {"number", "integer"} or any(word in keywords for word in ["revenue", "income", "cash", "assets"]):
            preferred = preferred or ["table_row", "table_cell"]
        label = field_path.replace("_", " ")
        query = " ".join([label, description]) if description else label
        return FieldRetrievalPlan(
            field_path=field_path,
            query=query or field_path,
            expected_type=expected_type,
            keywords=keywords,
            preferred_source_types=preferred,
            metadata_filters=dict(hints.get("metadata_filters") or {}),
            budget=budget_for_field(field_schema, settings),
        )


def _tokens(value: str) -> list[str]:
    """Split a text query into lower-case alphanumeric word tokens."""
    return [token for token in re.findall(r"[A-Za-z0-9]+", value.lower()) if len(token) > 1]
