from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.models.extraction_lab import ExtractionFieldResult, ExtractionLabSchema, ExtractionRunResponse, ExtractionRunStats, ExtractionSchemaField, MultiDocumentExtractionRunRequest
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


def test_llm_vlm_mode_requires_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extraction_lab, "_openai_api_key", lambda: "")
    schema = ExtractionLabSchema(fields=[ExtractionSchemaField(key="total", label="Total")])
    chunks = [extraction_lab.SourceChunk(id="c1", page=1, type="text", text="Total: 10")]

    with pytest.raises(HTTPException, match="OPENAI_API_KEY"):
        extraction_lab._reconstruct_relevant_evidence(schema, chunks, 3)


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
