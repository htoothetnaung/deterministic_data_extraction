from __future__ import annotations

from app.extraction.candidate_resolver import resolve_candidates
from app.extraction.field_extractor import ExtractedCandidate


def test_resolver_validates_agreement():
    value, status, confidence = resolve_candidates(
        [
            ExtractedCandidate(value=100, confidence=0.8, evidence_ids=["e1"]),
            ExtractedCandidate(value=100, confidence=0.7, evidence_ids=["e2"]),
        ]
    )

    assert value == 100
    assert status == "validated"
    assert confidence == 0.8


def test_resolver_flags_conflict():
    value, status, confidence = resolve_candidates(
        [
            ExtractedCandidate(value=100, confidence=0.8, evidence_ids=["e1"]),
            ExtractedCandidate(value=120, confidence=0.7, evidence_ids=["e2"]),
        ]
    )

    assert value == 100
    assert status == "conflict"
    assert confidence == 0.8

