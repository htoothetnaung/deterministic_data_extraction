from __future__ import annotations

from app.extraction.schema_constrained_extractor import clean_schema_value, validate_schema_value_quality


def test_clean_schema_value_removes_image_markers_html_and_links() -> None:
    value = '!![cover](/api/parser-benchmarks/media/x.png)<table><tr><td>AAA</td></tr></table> [RAM](https://example.test)'

    assert clean_schema_value(value, "string") == "AAA RAM"


def test_rejects_appendix_definition_as_document_title() -> None:
    errors = validate_schema_value_quality(
        "documentTitle",
        {"type": "string", "description": "Report title"},
        "CREDIT RATING DEFINITIONS",
    )

    assert errors


def test_rejects_ratings_dominated_by_headings() -> None:
    errors = validate_schema_value_quality(
        "ratings",
        {"type": "array", "description": "Credit ratings and rated instruments"},
        ["# CREDIT RATING RATIONALE", "!", "Financial Institution Ratings"],
    )

    assert "Ratings value does not look like rating/instrument entries" in errors


def test_accepts_real_rating_entries() -> None:
    errors = validate_schema_value_quality(
        "ratings",
        {"type": "array", "description": "Credit ratings and rated instruments"},
        ["Financial Institution Ratings: AAA/Stable/P1 [Reaffirmed]"],
    )

    assert errors == []


def test_rating_drivers_are_not_validated_as_rating_entries() -> None:
    errors = validate_schema_value_quality(
        "ratingDrivers",
        {"type": "string", "description": "Rating drivers and rationale"},
        "The rating is driven by Maybank IB's strategic role within the Maybank group.",
    )

    assert errors == []


def test_rejects_analysts_dominated_by_cover_headings() -> None:
    errors = validate_schema_value_quality(
        "analysts",
        {"type": "array", "description": "Analyst names"},
        ["CREDIT RATING RATIONALE", "Financial Institution Ratings"],
    )

    assert "analysts value is dominated by cover-page headings/noise" in errors


def test_allows_image_urls_for_image_fields() -> None:
    errors = validate_schema_value_quality(
        "keyFigures",
        {"type": "array", "description": "Relevant image or figure URLs"},
        ["/api/parser-benchmarks/media/mistral_ocr/abc/chart.png"],
    )

    assert errors == []
