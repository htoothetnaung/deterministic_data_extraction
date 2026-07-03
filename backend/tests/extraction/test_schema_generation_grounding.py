from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models.extraction_lab import SchemaGenerationRequest
from app.services.extraction_lab import _fallback_schema_from_context, generate_schema_definition


def test_schema_generation_requires_selected_document() -> None:
    with pytest.raises(HTTPException) as exc:
        generate_schema_definition(SchemaGenerationRequest(natural_language_query="extract ratings"))

    assert exc.value.status_code == 400
    assert "parsed document" in str(exc.value.detail)


def test_fallback_schema_uses_parser_evidence_not_prompt_keywords() -> None:
    schema = _fallback_schema_from_context(
        "extract ratings and stakeholders",
        [{"text": "This parser output only contains a management discussion and document overview."}],
    )

    keys = {field.key for field in schema.fields}
    assert "ratingDrivers" not in keys
    assert "stakeholders" not in keys
    assert "documentSummary" in keys


def test_fallback_schema_can_infer_fields_from_parser_evidence() -> None:
    schema = _fallback_schema_from_context(
        "",
        [{"text": "The rating rationale discusses liquidity, stakeholders, and financial position."}],
    )

    keys = {field.key for field in schema.fields}
    assert {"ratingDrivers", "financialPosition", "stakeholders"}.issubset(keys)
