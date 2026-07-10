from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.models.extraction_lab import (
    ExtractionLabSchema,
    ExtractionRunResponse,
    ExtractionRunStats,
    ExtractionSchemaField,
    MultiDocumentExtractionRunRequest,
)
from app.models.parser_benchmark import ParserInputInfo
from app.services import extraction_lab


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeSessionFactory:
    def __call__(self):
        return _FakeSession()


def _response(input_id: str) -> ExtractionRunResponse:
    now = datetime.now(timezone.utc)
    return ExtractionRunResponse(
        run_id=f"run-{input_id}",
        input=ParserInputInfo(id=input_id, name=f"{input_id}.pdf", input_type="pdf", size_bytes=1, path=""),
        parser_id="mistral_ocr",
        parser_name="Mistral OCR",
        schema_model_name="ExtractionResult",
        schema_definition={"name": "ExtractionResult", "fields": [{"key": "issuer", "label": "Issuer"}]},
        data={"issuer": input_id},
        fields=[],
        chunks=[],
        validation_errors=[],
        warnings=[],
        generated_code="",
        stats=ExtractionRunStats(
            parser_seconds=0,
            total_seconds=0,
            pages=1,
            chunks=1,
            fields=1,
            candidates_scanned=0,
        ),
        started_at=now,
        finished_at=now,
    )


@pytest.mark.anyio
async def test_per_document_multi_run_starts_documents_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.db.engine as db_engine

    active = 0
    max_active = 0
    overlap_seen = asyncio.Event()

    async def fake_run_extraction_db(session, payload):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active >= 2:
            overlap_seen.set()
        await asyncio.wait_for(overlap_seen.wait(), timeout=1)
        await asyncio.sleep(0.01)
        active -= 1
        return _response(payload.input_id)

    monkeypatch.setattr(extraction_lab, "resolve_input", lambda input_id: ParserInputInfo(id=input_id, name=f"{input_id}.pdf", input_type="pdf", size_bytes=1, path=""))
    monkeypatch.setattr(extraction_lab, "_has_existing_parser_result", lambda payload: True)
    monkeypatch.setattr(extraction_lab, "run_extraction_db", fake_run_extraction_db)
    monkeypatch.setattr(db_engine, "get_factory", lambda: _FakeSessionFactory())

    payload = MultiDocumentExtractionRunRequest(
        input_id="doc-a",
        input_ids=["doc-a", "doc-b", "doc-c", "doc-d", "doc-e", "doc-f"],
        output_schema=ExtractionLabSchema(fields=[ExtractionSchemaField(key="issuer", label="Issuer")]),
    )

    result = await extraction_lab.run_multi_document_extraction_db(_FakeSession(), payload)

    assert result.mode == "per_document"
    assert [item.input.id for item in result.results] == ["doc-a", "doc-b", "doc-c", "doc-d", "doc-e", "doc-f"]
    assert all(not item.input.id.startswith("bundle:") for item in result.results)
    assert max_active == 4


@pytest.mark.anyio
async def test_per_document_multi_run_validates_all_parser_outputs_before_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.db.engine as db_engine

    started: list[str] = []

    async def fake_run_extraction_db(session, payload):
        started.append(payload.input_id)
        return _response(payload.input_id)

    def fake_has_existing_parser_result(payload):
        return payload.input_id != "doc-b"

    monkeypatch.setattr(extraction_lab, "resolve_input", lambda input_id: ParserInputInfo(id=input_id, name=f"{input_id}.pdf", input_type="pdf", size_bytes=1, path=""))
    monkeypatch.setattr(extraction_lab, "_has_existing_parser_result", fake_has_existing_parser_result)
    monkeypatch.setattr(extraction_lab, "run_extraction_db", fake_run_extraction_db)
    monkeypatch.setattr(db_engine, "get_factory", lambda: _FakeSessionFactory())

    payload = MultiDocumentExtractionRunRequest(
        input_id="doc-a",
        input_ids=["doc-a", "doc-b", "doc-c"],
        output_schema=ExtractionLabSchema(fields=[ExtractionSchemaField(key="issuer", label="Issuer")]),
    )

    with pytest.raises(Exception, match="doc-b.pdf"):
        await extraction_lab.run_multi_document_extraction_db(_FakeSession(), payload)

    assert started == []
