from datetime import datetime, timezone
from decimal import Decimal

from app.db.repositories.job_repo import _json_safe


def test_json_safe_serializes_decimal_evidence_metadata() -> None:
    evidence_pack = {
        "text_snippets": [
            {
                "score": Decimal("0.8745"),
                "created_at": datetime(2026, 7, 10, tzinfo=timezone.utc),
            }
        ]
    }

    result = _json_safe(evidence_pack)

    assert result == {
        "text_snippets": [
            {"score": 0.8745, "created_at": "2026-07-10T00:00:00+00:00"}
        ]
    }
