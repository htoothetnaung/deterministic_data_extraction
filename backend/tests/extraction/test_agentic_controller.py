from __future__ import annotations

from app.extraction.agentic_controller import AgenticFieldExtractor, ConsistencyReport, critic_issues, detect_conflict
from app.extraction.field_extractor import FieldExtractor
from app.services.embedding import OPENAI_EMBEDDING_DIM
from app.services.production_extraction import _extractor_for_mode


def test_agentic_consistency_report_scores_penalties():
    report = ConsistencyReport(null_fields_detected=1, candidate_conflicts=1, critic_issues=["missing_required:total"])

    assert report.model_dump()["critic_issue_count"] == 1
    assert report.consistency_score == 0.55


def test_conflict_detection_normalizes_candidate_values():
    assert detect_conflict(["Revenue", " revenue "]) is False
    assert detect_conflict(["Revenue", "Assets"]) is True


def test_cross_field_accounting_critic_flags_mismatch():
    issues = critic_issues({"assets": 100, "liabilities": 40, "equity": 10}, set())

    assert "accounting_mismatch:assets_vs_liabilities_plus_equity" in issues


def test_openai_embedding_dim_is_1536():
    assert OPENAI_EMBEDDING_DIM == 1536


def test_extractor_for_mode_non_agentic_returns_field_extractor():
    extractor = _extractor_for_mode(agentic=False)
    assert isinstance(extractor, FieldExtractor)
    assert not isinstance(extractor, AgenticFieldExtractor)


def test_extractor_for_mode_agentic_returns_agentic_field_extractor():
    extractor = _extractor_for_mode(agentic=True)
    assert isinstance(extractor, AgenticFieldExtractor)
