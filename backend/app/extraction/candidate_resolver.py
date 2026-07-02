from __future__ import annotations

from typing import Any

from app.extraction.field_extractor import ExtractedCandidate


def resolve_candidates(candidates: list[ExtractedCandidate], conflict_policy: str = "human_review_on_disagreement") -> tuple[Any, str, float]:
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
    return " ".join(str(value).strip().lower().split())
