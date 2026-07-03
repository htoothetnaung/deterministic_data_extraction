from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.models.extraction_lab import ExtractionFieldResult, ExtractionLabSchema, ExtractionRunResponse, ExtractionRunStats, ExtractionSchemaField, MultiDocumentExtractionRunRequest, ExtractionRunRequest
from app.models.parser_benchmark import ParserArtifactPaths, ParserInputInfo, ParserRunResponse, ParserRunResult, ParserStatus
from app.services import evidence_cleaner
from app.services import extraction_lab
from app.services.parsers import persistence


def _result(run_id: str, library: str, output_path: Path, status: ParserStatus = ParserStatus.OK) -> ParserRunResult:
    return ParserRunResult(
        result_id=f"{run_id}:{library}",
        run_id=run_id,
        library=library,
        input_file="sample.pdf",
        input_type="pdf",
        status=status,
        seconds=1.0,
        pages=1,
        chars=100,
        tables=1,
        images=1,
        text_preview="preview",
        artifact_paths=ParserArtifactPaths(output_md=str(output_path)),
    )


def test_latest_ok_result_for_input_rehydrates_output_and_ignores_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(persistence, "runs_root", lambda: tmp_path)
    input_info = ParserInputInfo(id="data:sample.pdf", name="sample.pdf", input_type="pdf", size_bytes=10, path="sample.pdf")

    older_dir = tmp_path / "old"
    newer_dir = tmp_path / "new"
    failed_dir = tmp_path / "failed"
    for directory in [older_dir, newer_dir, failed_dir]:
        directory.mkdir()

    older_md = older_dir / "output.md"
    newer_md = newer_dir / "output.md"
    failed_md = failed_dir / "output.md"
    older_md.write_text("old markdown", encoding="utf-8")
    newer_md.write_text("new markdown", encoding="utf-8")
    failed_md.write_text("failed markdown", encoding="utf-8")

    runs = [
        ParserRunResponse(
            run_id="old",
            input=input_info,
            results=[_result("old", "mistral_ocr", older_md)],
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        ParserRunResponse(
            run_id="new",
            input=input_info,
            results=[_result("new", "mistral_ocr", newer_md)],
            started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
        ParserRunResponse(
            run_id="failed",
            input=input_info,
            results=[_result("failed", "mistral_ocr", failed_md, ParserStatus.FAILED)],
            started_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        ),
    ]
    for run in runs:
        (tmp_path / run.run_id / "run.json").write_text(json.dumps(run.model_dump(mode="json")), encoding="utf-8")

    latest = persistence.get_latest_ok_result_for_input("data:sample.pdf", "mistral_ocr")

    assert latest is not None
    run, result = latest
    assert run.run_id == "new"
    assert result.raw_text == "new markdown"


def test_cleaner_recovers_html_table_markdown_table_and_image() -> None:
    result = ParserRunResult(
        library="paddleocr_vl_vllm",
        input_file="sample.pdf",
        input_type="pdf",
        status=ParserStatus.OK,
        seconds=1,
        pages=1,
        chars=100,
        tables=0,
        images=0,
        raw_text="""<!-- page: 1 -->
<table><tr><th>Name</th><th>Amount</th></tr><tr><td>Revenue</td><td>100</td></tr></table>

| Item | Value |
| --- | --- |
| Cash | 50 |

![Chart](/api/parser-benchmarks/media/paddleocr_vl_vllm/a/page-001-image-01.jpg)
""",
    )

    cleaned = evidence_cleaner.clean_parser_result(result)

    assert cleaned["enabled"] is True
    assert cleaned["stats"]["tables"] >= 2
    assert cleaned["stats"]["images"] == 1
    assert any(item["rows"] for item in cleaned["items"] if item["type"] == "table")


def test_chunker_does_not_route_through_evidence_cleaner() -> None:
    """block/table_row chunking builds chunks directly from parser output."""
    import app.services.chunker as chunker

    result = ParserRunResult(
        library="mistral_ocr",
        input_file="sample.pdf",
        input_type="pdf",
        status=ParserStatus.OK,
        seconds=1,
        pages=1,
        chars=100,
        tables=1,
        images=0,
        structured_preview={
            "blocks": [
                {"type": "text", "page": 1, "text": "Revenue for the year was high."},
                {
                    "type": "table",
                    "page": 1,
                    "text": "",
                    "columns": ["item", "amount"],
                    "rows": [{"item": "Cash", "amount": "50"}],
                },
            ],
        },
        raw_text="<!-- page: 1 -->\nRevenue for the year was high.",
    )

    # The cleaner helper must be gone from the chunker module namespace.
    assert not hasattr(chunker, "cleaned_items_for_extraction")

    block_chunks = chunker.chunk_parser_result(result, strategy="block")
    row_chunks = chunker.chunk_parser_result(result, strategy="table_row")

    assert block_chunks, "block strategy should produce chunks from parser blocks"
    assert any(c.chunk_type == "table" for c in block_chunks)
    assert any(c.chunk_type == "table_row" for c in row_chunks), "table_row strategy should decompose tables"


def test_table_extraction_merges_multiple_relevant_tables_and_image_lists() -> None:
    table_field = ExtractionSchemaField(
        key="financial_statement_tables",
        label="Financial Statement Tables",
        type="table",
        description="balance sheet assets liabilities",
    )
    image_field = ExtractionSchemaField(
        key="relevant_images",
        label="Relevant Images",
        type="list",
        description="images and figures",
    )
    chunks = [
        extraction_lab.SourceChunk(
            id="tbl-1",
            page=2,
            type="table",
            text="Assets",
            rows=[{"item": "Assets", "amount": "100"}],
        ),
        extraction_lab.SourceChunk(
            id="tbl-2",
            page=3,
            type="table",
            text="Liabilities",
            rows=[{"item": "Liabilities", "amount": "40"}],
        ),
        extraction_lab.SourceChunk(
            id="img-1",
            page=4,
            type="image",
            text="Balance sheet chart",
            source_url="/api/parser-benchmarks/media/chart.jpg",
        ),
    ]

    table_result, _ = extraction_lab._extract_field(table_field, chunks, 8)
    image_result, _ = extraction_lab._extract_field(image_field, chunks, 8)

    assert isinstance(table_result.value, list)
    assert len(table_result.value) == 2
    assert table_result.value[0]["_evidence_page"] == "2"
    assert image_result.value == ["Balance sheet chart (/api/parser-benchmarks/media/chart.jpg)"]


def test_lab_schema_maps_to_path_b_json_schema() -> None:
    schema = ExtractionLabSchema(
        fields=[
            ExtractionSchemaField(key="total", label="Total", type="number", required=True),
            ExtractionSchemaField(key="approved", label="Approved", type="boolean"),
        ]
    )

    mapped = extraction_lab._lab_schema_to_json_schema(schema)

    assert mapped["required"] == ["total"]
    assert mapped["properties"]["total"]["type"] == "number"
    assert mapped["properties"]["approved"]["type"] == "boolean"


def test_multi_document_payload_maps_to_single_payload() -> None:
    schema = ExtractionLabSchema(fields=[ExtractionSchemaField(key="total", label="Total")])
    payload = MultiDocumentExtractionRunRequest(input_id="ignored", input_ids=["doc:a", "doc:b"], output_schema=schema)

    single = extraction_lab._single_payload(payload, "doc:b")

    assert single.input_id == "doc:b"
    assert single.output_schema.fields[0].key == "total"


def test_schema_with_query_preserves_fields_and_appends_hint() -> None:
    schema = ExtractionLabSchema(
        fields=[
            ExtractionSchemaField(key="total", label="Total", type="number", required=True, description="the total"),
            ExtractionSchemaField(key="approved", label="Approved", type="boolean"),
        ]
    )
    result = extraction_lab._schema_with_query(schema, "look for the grand total")
    assert len(result.fields) == 2
    assert result.fields[0].key == "total"
    assert result.fields[1].key == "approved"
    assert "look for the grand total" in result.fields[0].description
    assert "look for the grand total" in result.fields[1].description

    json_schema = extraction_lab._lab_schema_to_json_schema(result)
    assert "total" in json_schema["properties"]
    assert "approved" in json_schema["properties"]
    assert json_schema["required"] == ["total"]


def test_report_generation_requires_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extraction_lab, "_openai_api_key", lambda: "")
    schema = ExtractionLabSchema(fields=[ExtractionSchemaField(key="total", label="Total")])
    result = ExtractionRunResponse(
        run_id="ext-test",
        input=ParserInputInfo(id="data:sample.pdf", name="sample.pdf", input_type="pdf", size_bytes=10, path="sample.pdf"),
        parser_id="mistral_ocr",
        parser_name="Mistral OCR",
        schema_model_name="ExtractionResult",
        schema_definition=schema.model_dump(mode="json"),
        data={"total": "10"},
        fields=[
            ExtractionFieldResult(
                key="total",
                label="Total",
                type="text",
                required=False,
                value="10",
                raw_value="10",
                confidence=0.9,
            )
        ],
        chunks=[],
        generated_code="",
        stats=ExtractionRunStats(parser_seconds=1, total_seconds=1, pages=1, chunks=0, fields=1, candidates_scanned=1),
    )

    with pytest.raises(HTTPException, match="OPENAI_API_KEY"):
        extraction_lab.generate_polished_report(result)


def test_has_existing_parser_result_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Unknown input
    monkeypatch.setattr(extraction_lab, "resolve_input", lambda x: None)
    req = ExtractionRunRequest(
        input_id="unknown_id",
        output_schema=ExtractionLabSchema(),
        parser_id="auto",
        chunking_strategy="page",
        chunk_size=1000,
        chunk_overlap=100,
        max_pages=2,
        max_candidates_per_field=2,
        preview_chars=1000,
        extraction_tier="cost_effective"
    )
    assert not extraction_lab._has_existing_parser_result(req)

    # 2. Text input
    txt_input = ParserInputInfo(id="text_id", name="text.txt", input_type="text", size_bytes=10, path="text.txt")
    monkeypatch.setattr(extraction_lab, "resolve_input", lambda x: txt_input)
    assert extraction_lab._has_existing_parser_result(req)

    # 3. PDF input with existing parser result
    pdf_input = ParserInputInfo(id="pdf_id", name="doc.pdf", input_type="pdf", size_bytes=10, path="doc.pdf")
    monkeypatch.setattr(extraction_lab, "resolve_input", lambda x: pdf_input)
    monkeypatch.setattr(extraction_lab, "get_latest_ok_result_for_input", lambda input_id, parser_id: ("run", "result"))
    assert extraction_lab._has_existing_parser_result(req)

    # 4. PDF input with NO existing parser result
    monkeypatch.setattr(extraction_lab, "get_latest_ok_result_for_input", lambda input_id, parser_id: None)
    assert not extraction_lab._has_existing_parser_result(req)


def test_append_missed_fields_table() -> None:
    schema = ExtractionLabSchema(fields=[
        ExtractionSchemaField(key="total", label="Total"),
        ExtractionSchemaField(key="approved", label="Approved", required=True)
    ])
    result = ExtractionRunResponse(
        run_id="ext-test",
        input=ParserInputInfo(id="data:sample.pdf", name="sample.pdf", input_type="pdf", size_bytes=10, path="sample.pdf"),
        parser_id="mistral_ocr",
        parser_name="Mistral OCR",
        schema_model_name="ExtractionResult",
        schema_definition=schema.model_dump(mode="json"),
        data={"total": "10", "approved": None},
        fields=[
            ExtractionFieldResult(
                key="total",
                label="Total",
                type="text",
                required=False,
                value="10",
                raw_value="10",
                confidence=0.9,
            ),
            ExtractionFieldResult(
                key="approved",
                label="Approved",
                type="boolean",
                required=True,
                value=None,
                raw_value=None,
                confidence=0.0,
                valid=False,
                validation_message="Value missing"
            )
        ],
        chunks=[],
        generated_code="",
        stats=ExtractionRunStats(parser_seconds=1, total_seconds=1, pages=1, chunks=0, fields=2, candidates_scanned=1),
    )
    
    report = "This is a polished report."
    appended = extraction_lab._append_missed_fields_table(report, result)
    assert "## Missed fields" in appended
    assert "| Approved | `approved` | boolean | Yes | Missing | Value missing |" in appended


def test_strip_html_removes_table_tags_and_entities() -> None:
    from app.extraction.field_extractor import _strip_html

    raw = "<table><tr><td>Revenue &amp; Profit</td></tr></table>"
    result = _strip_html(raw)
    assert "<table>" not in result
    assert "&amp;" not in result
    assert "&" in result


def test_clean_value_strips_html_and_markdown_noise() -> None:
    from app.extraction.field_extractor import _clean_value

    raw = "Hello <b>world</b> ![img](url) [link](http://x) <br> <p>para</p>"
    result = _clean_value(raw, "string")
    assert "<b>" not in result
    assert "![img](url)" not in result
    assert "<br>" not in result
    assert "<p>" not in result
    assert "Hello" in result
    assert "world" in result


def test_clean_value_returns_none_for_empty_string() -> None:
    from app.extraction.field_extractor import _clean_value

    assert _clean_value("  \n  ", "string") is None
    assert _clean_value("| - |", "string") is None
    assert _clean_value(None, "string") is None


def test_fallback_documentTitle_uses_first_heading() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = "# Credit Rating Rationale\n\nMalayan Banking Berhad"
    result = _fallback_extract("documentTitle", {"type": "string"}, "string", text, FieldIntent.TITLE)
    assert result is not None
    assert "Credit Rating Rationale" in result


def test_fallback_reportDate_parses_month_year() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = "Date: November 2012\n\nRated entity: Malayan Banking Berhad"
    result = _fallback_extract("reportDate", {"type": "string"}, "string", text, FieldIntent.DATE)
    assert result is not None
    assert "November 2012" in result


def test_fallback_companyName_parses_rated_entity() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = "Rated Entity: Malayan Banking Berhad\n\nCredit Rating Rationale"
    result = _fallback_extract("companyName", {"type": "string"}, "string", text, FieldIntent.ISSUER)
    assert result is not None
    assert "Malayan Banking Berhad" in result


def test_fallback_summary_parses_summary_section() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = (
        "# Summary\n\n"
        "MARC has affirmed Malayan Banking Berhad's AAA rating. "
        "The outlook is stable. This is based on the bank's strong franchise.\n\n"
        "# Rating Drivers"
    )
    result = _fallback_extract("summary", {"type": "string"}, "string", text, FieldIntent.SUMMARY)
    assert result is not None
    assert "MARC" in result or "affirmed" in result


def test_fallback_ratingDrivers_excludes_definition_section() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = (
        "Rating Drivers\n\n"
        "Strong market position in domestic banking.\n"
        "Well-diversified revenue streams.\n\n"
        "CREDIT RATING DEFINITIONS\n\n"
        "AAA: Highest credit quality."
    )
    result = _fallback_extract("ratingDrivers", {"type": "string"}, "string", text, FieldIntent.RATING_DRIVERS)
    assert result is not None
    assert "CREDIT RATING DEFINITIONS" not in result
    assert "Strong market position" in result


def test_fallback_analysts_parses_analyst_block() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = (
        "Analysts:\n"
        "John Doe\n"
        "Jane Smith\n"
        "john.doe@marc.com.my\n"
        "Disclaimer: This report is prepared for..."
    )
    result = _fallback_extract("analysts", {"type": "array"}, "array", text, FieldIntent.ANALYSTS)
    assert result is not None
    assert "John Doe" in result
    assert "Jane Smith" in result


def test_fallback_subsidiaries_excludes_financial_tables() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = (
        "Subsidiaries\n\n"
        "Malayan Banking Berhad\n"
        "Maybank Islamic Berhad\n"
        "Total assets 100,000\n"
        "Income statement\n"
        "Note: All amounts in MYR"
    )
    result = _fallback_extract("subsidiariesAndAssociates", {"type": "array"}, "array", text, FieldIntent.SUBSIDIARIES)
    assert result is not None
    assert "Malayan Banking Berhad" in result
    assert "Total assets" not in str(result)
    assert "Income statement" not in str(result)


def test_financial_table_rejected_for_narrative_field() -> None:
    from app.extraction.field_extractor import (
        _extract_structured_value,
        _is_financial_or_definition_table,
    )

    item = {
        "source_type": "table_row",
        "text": "| Revenue | 100 |\n| Cost | 50 |",
        "metadata_json": {"columns": ["Item", "Amount"]},
    }
    assert _is_financial_or_definition_table(item, item["text"]) is True

    result = _extract_structured_value("array", item, item["text"], "narrativeField")
    assert result is None, "financial table should be rejected for narrative fields"


def test_detect_intent_title_aliases() -> None:
    from app.extraction.field_extractor import FieldIntent, _detect_field_intent

    assert _detect_field_intent("documentName", {"type": "string"}) == FieldIntent.TITLE
    assert _detect_field_intent("documentTitle", {"type": "string"}) == FieldIntent.TITLE
    assert _detect_field_intent("title", {"type": "string"}) == FieldIntent.TITLE
    assert _detect_field_intent("reportName", {"type": "string"}) == FieldIntent.TITLE


def test_detect_intent_date_aliases() -> None:
    from app.extraction.field_extractor import FieldIntent, _detect_field_intent

    assert _detect_field_intent("reportingPeriod", {"type": "string"}) == FieldIntent.DATE
    assert _detect_field_intent("reportDate", {"type": "string"}) == FieldIntent.DATE
    assert _detect_field_intent("period", {"type": "string"}) == FieldIntent.DATE


def test_detect_intent_issuer_aliases() -> None:
    from app.extraction.field_extractor import FieldIntent, _detect_field_intent

    assert _detect_field_intent("issuer", {"type": "string"}) == FieldIntent.ISSUER
    assert _detect_field_intent("ratedEntity", {"type": "string"}) == FieldIntent.ISSUER
    assert _detect_field_intent("entity", {"type": "string", "label": "Rated Entity"}) == FieldIntent.ISSUER


def test_detect_intent_ratings() -> None:
    from app.extraction.field_extractor import FieldIntent, _detect_field_intent

    assert _detect_field_intent("ratings", {"type": "array"}) == FieldIntent.RATINGS
    assert _detect_field_intent("creditRatings", {"type": "array"}) == FieldIntent.RATINGS
    assert _detect_field_intent("financialInstitutionRatings", {"type": "array"}) == FieldIntent.RATINGS


def test_fallback_documentName_via_intent() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = "# Credit Rating Rationale\n\nMaybank Investment Bank Berhad"
    result = _fallback_extract("documentName", {"type": "string"}, "string", text, FieldIntent.TITLE)
    assert result is not None
    assert "Credit Rating Rationale" in result


def test_fallback_reportingPeriod_via_intent() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = "Dated: January 2019 (Updated 2 January 2019)\n\nRated Entity: Maybank"
    result = _fallback_extract("reportingPeriod", {"type": "string"}, "string", text, FieldIntent.DATE)
    assert result is not None
    assert "January 2019" in result


def test_fallback_issuer_via_intent() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = "Rated Entity: Maybank Investment Bank Berhad\n\nCredit Rating Rationale"
    result = _fallback_extract("issuer", {"type": "string"}, "string", text, FieldIntent.ISSUER)
    assert result is not None
    assert "Maybank Investment Bank Berhad" in result


def test_fallback_issuer_uses_first_entity_line() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = "Maybank Investment Bank Berhad\n\n# Credit Rating Rationale"
    result = _fallback_extract("issuer", {"type": "string"}, "string", text, FieldIntent.ISSUER)
    assert result is not None
    assert "Maybank Investment Bank Berhad" in result


def test_extract_structured_value_rejects_noise_lines() -> None:
    from app.extraction.field_extractor import _extract_structured_value

    item = {
        "source_type": "text_block",
        "text": "# CREDIT RATING RATIONALE\n\nMaybank\n![]\n\nSome actual data",
        "metadata_json": {},
    }
    result = _extract_structured_value("array", item, item["text"], "someField")
    assert result is not None
    assert all("CREDIT RATING RATIONALE" not in str(r) for r in result)
    assert all("![]" not in str(r) for r in result)
    assert any("Maybank" in str(r) or "actual data" in str(r) for r in result)


def test_fallback_ratings_does_not_include_cover_heading() -> None:
    from app.extraction.field_extractor import FieldIntent, _fallback_extract

    text = (
        "# CREDIT RATING RATIONALE\n\n"
        "Financial Institution Ratings\n\n"
        "Maybank Investment Bank Berhad\n"
        "Long-Term Rating      AAA\n"
        "Short-Term Rating     MARC-1\n\n"
        "Disclaimer: This report is prepared for..."
    )
    result = _fallback_extract("ratings", {"type": "array"}, "array", text, FieldIntent.RATINGS)
    assert result is not None
    assert all("CREDIT RATING RATIONALE" not in str(r) for r in result)
    assert any("AAA" in str(r) for r in result)


def test_sanitize_rejects_cover_heading_with_bangs() -> None:
    from app.extraction.field_extractor import sanitize_extracted_value

    assert sanitize_extracted_value("RATING RATIONALE ! !", "summary", "string") is None
    assert sanitize_extracted_value("Credit Rating Rationale", "documentTitle", "string") is None
    assert sanitize_extracted_value("Financial Institution Ratings", "ratings", "string") is None


def test_sanitize_strips_markdown_bold_from_value() -> None:
    from app.extraction.field_extractor import sanitize_extracted_value

    result = sanitize_extracted_value("**RAM Rating Services Berhad**", "analysts", "string")
    assert result is not None
    assert "**" not in result


def test_sanitize_rejects_punctuation_only_noise() -> None:
    from app.extraction.field_extractor import sanitize_extracted_value

    assert sanitize_extracted_value("! ! ! - - -", "summary", "string") is None
    assert sanitize_extracted_value("###", "documentTitle", "string") is None


def test_sanitize_rejects_image_markers_for_non_image_fields() -> None:
    from app.extraction.field_extractor import sanitize_extracted_value

    assert sanitize_extracted_value("![image](http://x.com/logo.png)", "companyName", "string") is None
    assert sanitize_extracted_value("![chart](http://x.com/chart.jpg)", "financialPerformance", "string") is None


def test_sanitize_allows_image_urls_for_image_fields() -> None:
    from app.extraction.field_extractor import sanitize_extracted_value

    result = sanitize_extracted_value("![logo](http://x.com/logo.png)", "logoImage", "string")
    assert result is not None
    assert "logo.png" in result


def test_sanitize_rejects_rating_agency_as_report_title() -> None:
    from app.extraction.field_extractor import sanitize_extracted_value

    result = sanitize_extracted_value("RAM Rating Services Berhad", "documentTitle", "string")
    assert result is None


def test_sanitize_preserves_valid_narrative_values() -> None:
    from app.extraction.field_extractor import sanitize_extracted_value

    result = sanitize_extracted_value(
        "The bank maintains strong capital adequacy with tier-1 ratio above 12%.",
        "financialPerformance",
        "string",
    )
    assert result is not None
    assert "capital adequacy" in result


def test_sanitize_cleans_list_values() -> None:
    from app.extraction.field_extractor import sanitize_extracted_value

    result = sanitize_extracted_value(
        ["Funding and liquidity remain strong.", "RATING RATIONALE ! !", "**bold text**"],
        "funding",
        "array",
    )
    assert result is not None
    assert len(result) == 2
    assert all("RATING RATIONALE" not in str(v) for v in result)
    assert all("**" not in str(v) for v in result)


def test_production_pipeline_clean_items_to_chunks() -> None:
    from app.services.production_pipeline import _clean_items_to_chunks

    items = [
        {
            "id": "ev-1",
            "page": 1,
            "type": "text",
            "text": "Summary of credit rating",
            "bbox": None,
            "confidence": 0.9,
            "risk": "normal",
            "warnings": [],
            "provenance": {"source": "evidence_cleaner.block"},
            "columns": [],
            "rows": [],
        },
        {
            "id": "ev-2",
            "page": 2,
            "type": "table",
            "text": "",
            "bbox": {"x0": 10, "top": 20},
            "confidence": 0.58,
            "risk": "financial_review",
            "warnings": [],
            "provenance": {"source": "evidence_cleaner.raw_markdown_table"},
            "columns": ["Item", "Amount"],
            "rows": [{"Item": "Revenue", "Amount": "100"}],
        },
    ]
    chunks = _clean_items_to_chunks(items)
    assert len(chunks) == 2
    assert chunks[0].chunk_type == "text"
    assert chunks[1].chunk_type == "table"
    assert chunks[1].rows is not None and len(chunks[1].rows) == 1
