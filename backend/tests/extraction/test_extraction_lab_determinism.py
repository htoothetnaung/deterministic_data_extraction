from __future__ import annotations

from pathlib import Path

from app.models.extraction_lab import (
    ExtractionLabSchema,
    ExtractionRunRequest,
    ExtractionRunResponse,
    ExtractionRunStats,
    ExtractionSchemaField,
)
from app.models.parser_benchmark import ParserInputInfo, ParserRunResult, ParserStatus
from app.services import extraction_lab


def test_extraction_lab_replays_cached_successful_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    schema = ExtractionLabSchema(fields=[ExtractionSchemaField(key="documentName", label="Document Name")])
    payload = ExtractionRunRequest(input_id="data:maybank.pdf", output_schema=schema)
    parser_result = ParserRunResult(
        result_id="result-1",
        run_id="parser-run-1",
        library="mistral_ocr",
        input_file="Maybank.pdf",
        input_type="pdf",
        status=ParserStatus.OK,
        seconds=1,
        pages=21,
        chars=1000,
        tables=0,
        images=0,
        text_preview="RAM CREDIT RATING RATIONALE",
    )
    cache_key = extraction_lab._deterministic_cache_key(payload, "mistral_ocr", parser_result)
    response = ExtractionRunResponse(
        run_id="job-stable",
        input=ParserInputInfo(id=payload.input_id, name="Maybank.pdf", input_type="pdf", size_bytes=10, path="Maybank.pdf"),
        parser_id="mistral_ocr",
        parser_name="Mistral OCR",
        schema_model_name="ExtractionResult",
        schema_definition=schema.model_dump(mode="json"),
        data={"documentName": "RAM CREDIT RATING RATIONALE"},
        fields=[],
        chunks=[],
        generated_code="",
        stats=ExtractionRunStats(parser_seconds=1, total_seconds=1, pages=21, chunks=1, fields=1, candidates_scanned=1),
    )

    extraction_lab._write_cached_result(cache_key, response)

    cached = extraction_lab._read_cached_result(cache_key)

    assert cached is not None
    assert cached.run_id == "job-stable"
    assert cached.data == {"documentName": "RAM CREDIT RATING RATIONALE"}
