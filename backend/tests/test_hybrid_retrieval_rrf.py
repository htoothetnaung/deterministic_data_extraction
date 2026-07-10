from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.db.repositories import evidence_repo


def _mock_session(hybrid_rows=None, fts_rows=None, keyword_rows=None):
    """Build an AsyncMock session that returns scripted mappings for each SQL type."""
    session = MagicMock()

    hybrid_mappings = (
        [{**row, "page_number": 1, "document_id": "d1", "bbox": None, "confidence": None, "metadata_json": {}} for row in hybrid_rows]
        if hybrid_rows else []
    )
    fts_mappings = (
        [{**row, "page_number": 1, "document_id": "d1", "bbox": None, "confidence": None, "metadata_json": {}} for row in fts_rows]
        if fts_rows else []
    )
    keyword_mappings = (
        [{**row} for row in keyword_rows] if keyword_rows else []
    )

    async def _execute(stmt, params=None):
        result = MagicMock()
        if stmt is evidence_repo.HYBRID_SEARCH_SQL:
            result.mappings = MagicMock(return_value=hybrid_mappings)
        elif stmt is evidence_repo.FTS_ONLY_SQL:
            result.mappings = MagicMock(return_value=fts_mappings)
        else:
            result.mappings = MagicMock(return_value=keyword_mappings)
        return result

    session.execute = AsyncMock(side_effect=_execute)
    return session


def test_hybrid_search_with_embedding_returns_hybrid_sql_rows() -> None:
    hybrid_rows = [
        {"evidence_id": "e1", "hybrid_score": 0.85, "source_type": "text_block", "text": "revenue growth", "markdown": None},
        {"evidence_id": "e2", "hybrid_score": 0.72, "source_type": "text_block", "text": "revenue decline", "markdown": None},
    ]
    session = _mock_session(hybrid_rows=hybrid_rows)
    repo = evidence_repo.EvidenceRepository(session)

    rows = asyncio.run(repo.hybrid_search(case_id="c1", query="revenue", query_embedding=[0.1] * 3, top_k=3))

    assert len(rows) == 2
    assert rows[0]["evidence_id"] == "e1"


def test_hybrid_search_without_embedding_uses_fts_sql() -> None:
    fts_rows = [
        {"evidence_id": "e1", "score": 0.9, "source_type": "text_block", "text": "revenue recognition", "markdown": None},
    ]
    session = _mock_session(fts_rows=fts_rows)
    repo = evidence_repo.EvidenceRepository(session)

    rows = asyncio.run(repo.hybrid_search(case_id="c1", query="revenue", query_embedding=None, top_k=3))

    assert len(rows) == 1
    assert rows[0]["evidence_id"] == "e1"


def test_hybrid_search_falls_back_to_keyword_when_no_rows() -> None:
    keyword_rows = [
        {"evidence_id": "e1", "source_type": "text_block", "text": "found via keyword ILIKE", "markdown": None, "page_number": 1, "document_id": "d1", "bbox": None, "confidence": None, "metadata_json": {}},
    ]
    session = _mock_session(keyword_rows=keyword_rows)
    repo = evidence_repo.EvidenceRepository(session)

    rows = asyncio.run(repo.hybrid_search(case_id="c1", query="revenue", query_embedding=[0.1] * 3, top_k=3))

    assert len(rows) == 1
    assert rows[0]["evidence_id"] == "e1"


def test_hybrid_search_top_k_is_respected() -> None:
    hybrid_rows = [
        {"evidence_id": f"e{i}", "hybrid_score": 1.0 - i * 0.1, "source_type": "text_block", "text": f"result {i}", "markdown": None}
        for i in range(10)
    ]
    session = _mock_session(hybrid_rows=hybrid_rows)
    repo = evidence_repo.EvidenceRepository(session)

    rows = asyncio.run(repo.hybrid_search(case_id="c1", query="test", query_embedding=[0.1] * 3, top_k=3))

    assert len(rows) <= 3


def test_hybrid_search_empty_corpus_returns_empty() -> None:
    session = _mock_session()
    repo = evidence_repo.EvidenceRepository(session)

    rows = asyncio.run(repo.hybrid_search(case_id="c1", query="revenue", query_embedding=[0.1] * 3, top_k=3))

    assert rows == []


def test_hybrid_search_applies_source_type_filter() -> None:
    hybrid_rows = [
        {"evidence_id": "e1", "hybrid_score": 0.8, "source_type": "table_row", "text": "table data", "markdown": None},
    ]
    session = _mock_session(hybrid_rows=hybrid_rows)
    repo = evidence_repo.EvidenceRepository(session)

    rows = asyncio.run(repo.hybrid_search(case_id="c1", query="data", query_embedding=[0.1] * 3, top_k=5, source_type_filter="table_row"))

    assert rows
    assert rows[0]["source_type"] == "table_row"


def test_hybrid_search_accepts_custom_rrf_parameters() -> None:
    hybrid_rows = [
        {"evidence_id": "e1", "hybrid_score": 0.95, "source_type": "text_block", "text": "value", "markdown": None},
    ]
    session = _mock_session(hybrid_rows=hybrid_rows)
    repo = evidence_repo.EvidenceRepository(session)

    rows = asyncio.run(repo.hybrid_search(
        case_id="c1",
        query="data",
        query_embedding=[0.1] * 3,
        top_k=5,
        dense_limit=15,
        sparse_limit=25,
        rrf_k=50,
    ))

    assert len(rows) == 1
    session.execute.assert_called_once()
    called_args = session.execute.call_args[0]
    params = called_args[1]
    assert params["dense_limit"] == 15
    assert params["sparse_limit"] == 25
    assert params["rrf_k"] == 50