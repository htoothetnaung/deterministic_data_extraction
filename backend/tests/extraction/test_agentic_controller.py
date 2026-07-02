from __future__ import annotations

from app.extraction.agentic_controller import ConsistencyReport, critic_issues, detect_conflict
from app.services.embedding import GEMINI_EMBEDDING_DIM


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


def test_gemini_embedding_dim_is_3072():
    assert GEMINI_EMBEDDING_DIM == 3072
