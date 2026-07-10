"""Candidate resolver for structured data extraction.

Resolves final values and confidence states when multiple extraction candidates
are retrieved across different document segments or parsing passes.
"""
from __future__ import annotations

from typing import Any

from app.extraction.field_extractor import ExtractedCandidate


def resolve_candidates(candidates: list[ExtractedCandidate], conflict_policy: str = "human_review_on_disagreement") -> tuple[Any, str, float]:
    """Resolve multiple value candidates extracted for a field into a single final selection.

    Workflow:
    1. Returns 'missing' if no candidate list is supplied.
    2. Sorts candidates by confidence (highest first) and metadata attributes.
    3. Normalizes candidate values to compare distinct entries.
    4. If values disagree and the policy requires review, returns 'conflict'.
    5. If the highest confidence candidate is below 0.7, returns 'low_confidence'.
    6. Otherwise, returns 'validated' along with the resolved value and confidence.
    """
    if not candidates:
        return None, "missing", 0.0
    ranked = sorted(
        candidates,
        key=lambda item: (
            -item.confidence,
            str(item.value),
            ",".join(item.evidence_ids),
            item.extraction_method,
        ),
    )
    selected = ranked[0]
    normalized = {_normal(candidate.value) for candidate in ranked if candidate.value is not None}
    if len(normalized) > 1 and conflict_policy == "human_review_on_disagreement":
        return selected.value, "conflict", selected.confidence
    if selected.confidence < 0.7:
        return selected.value, "low_confidence", selected.confidence
    return selected.value, "validated", selected.confidence


def _normal(value: Any) -> str:
    """Normalize whitespace and lower-case values to make string comparisons robust."""
    return " ".join(str(value).strip().lower().split())
