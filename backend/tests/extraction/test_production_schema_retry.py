from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.extraction.evidence_pack import EvidencePack
from app.extraction.schema_constrained_extractor import SchemaExtractionResult, SchemaFieldAudit
from app.models.extraction import ExtractionRequest
from app.services import production_extraction


class _FakeJobRepo:
    def __init__(self) -> None:
        self.candidates = []
        self.status = None

    async def add_field_result(self, job_id: str, field_path: str):
        return SimpleNamespace(field_result_id=f"field-{field_path}")

    async def add_attempt(self, *args, **kwargs):
        return SimpleNamespace()

    async def add_candidate(self, *args, **kwargs):
        self.candidates.append(kwargs)
        return SimpleNamespace()

    async def update_status(self, job_id: str, status: str):
        self.status = status
        return SimpleNamespace(job_id=job_id, status=status)


class _FakeRetriever:
    retrieval_stats = SimpleNamespace(mode="test", dense_hits=0, sparse_hits=0)

    async def retrieve(self, case_id: str, plan, attempt: int, *args, **kwargs):
        return EvidencePack(
            field_path="issuer",
            query="issuer",
            text_snippets=[{"evidence_id": "ev-1", "text": "No matching value here."}],
            estimated_text_tokens=4,
        )


class _FakePlanner:
    def plan(self, field_path: str, field_schema: dict, *args, **kwargs):
        return SimpleNamespace(field_path=field_path)


class _FakeSession:
    async def commit(self) -> None:
        return None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _empty_cover_evidence(session, case_id: str) -> list:
    return []


def _missing_schema_result(schema: dict, field_packs: dict, cover_evidence: list, document_map):
    audit = {
        key: SchemaFieldAudit(field_path=key, field_schema=field_schema)
        for key, field_schema in schema["properties"].items()
    }
    return SchemaExtractionResult(
        data={"issuer": None},
        confidence_by_field={},
        evidence_ids_by_field={},
        audit=audit,
        used_llm=False,
        error=None,
    )


def _successful_schema_result(schema: dict, field_packs: dict, cover_evidence: list, document_map):
    audit = {
        key: SchemaFieldAudit(field_path=key, field_schema=field_schema)
        for key, field_schema in schema["properties"].items()
    }
    return SchemaExtractionResult(
        data={"issuer": "Maybank Investment Bank Berhad"},
        confidence_by_field={"issuer": 0.88},
        evidence_ids_by_field={"issuer": ["ev-1"]},
        audit=audit,
        used_llm=True,
        error=None,
    )


@pytest.mark.anyio
async def test_schema_mode_empty_retry_marks_needs_review_not_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSchemaExtractor:
        def extract(self, schema: dict, field_packs: dict, cover_evidence: list, document_map):
            return _missing_schema_result(schema, field_packs, cover_evidence, document_map)

    class FakeAgenticExtractor:
        def extract(self, field_path: str, field_schema: dict, pack: EvidencePack):
            return []

    monkeypatch.setattr(production_extraction, "_case_cover_evidence", _empty_cover_evidence)
    monkeypatch.setattr(production_extraction, "SchemaConstrainedExtractor", FakeSchemaExtractor)
    monkeypatch.setattr(production_extraction, "AgenticFieldExtractor", FakeAgenticExtractor)
    monkeypatch.setattr(production_extraction, "write_extraction_audit", lambda *args, **kwargs: "audit.json")

    job_repo = _FakeJobRepo()
    result = await production_extraction._run_schema_constrained_case_extraction_db(
        session=_FakeSession(),
        case_id="case-1",
        payload=ExtractionRequest(
            schema_id="test",
            output_schema={
                "type": "object",
                "properties": {"issuer": {"type": "string"}},
                "required": ["issuer"],
            },
        ),
        schema={"type": "object", "properties": {"issuer": {"type": "string"}}, "required": ["issuer"]},
        job=SimpleNamespace(job_id="job-1", started_at=datetime.now(timezone.utc), completed_at=None),
        job_repo=job_repo,
        planner=_FakePlanner(),
        retriever=_FakeRetriever(),
    )

    assert job_repo.status == "needs_review"
    assert result.status == "needs_review"
    assert result.fields["issuer"].status == "missing"
    assert job_repo.candidates == []


@pytest.mark.anyio
async def test_schema_mode_llm_candidate_uses_supported_extraction_method(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSchemaExtractor:
        def extract(self, schema: dict, field_packs: dict, cover_evidence: list, document_map):
            return _successful_schema_result(schema, field_packs, cover_evidence, document_map)

    monkeypatch.setattr(production_extraction, "_case_cover_evidence", _empty_cover_evidence)
    monkeypatch.setattr(production_extraction, "SchemaConstrainedExtractor", FakeSchemaExtractor)
    monkeypatch.setattr(production_extraction, "write_extraction_audit", lambda *args, **kwargs: "audit.json")

    job_repo = _FakeJobRepo()
    result = await production_extraction._run_schema_constrained_case_extraction_db(
        session=_FakeSession(),
        case_id="case-1",
        payload=ExtractionRequest(
            schema_id="test",
            output_schema={
                "type": "object",
                "properties": {"issuer": {"type": "string"}},
                "required": ["issuer"],
            },
        ),
        schema={"type": "object", "properties": {"issuer": {"type": "string"}}, "required": ["issuer"]},
        job=SimpleNamespace(job_id="job-1", started_at=datetime.now(timezone.utc), completed_at=None),
        job_repo=job_repo,
        planner=_FakePlanner(),
        retriever=_FakeRetriever(),
    )

    assert result.status == "completed"
    assert result.fields["issuer"].candidates[0].extraction_method == "llm_text"
    assert job_repo.candidates[0]["extraction_method"] == "llm_text"
