from __future__ import annotations

from app.extraction.context_budget import ContextBudget
from app.extraction.evidence_pack import build_evidence_pack
from app.extraction.field_extractor import FieldExtractor


def test_evidence_pack_respects_token_and_item_budget():
    rows = [
        {"evidence_id": "e1", "source_type": "text_block", "text": "a" * 400},
        {"evidence_id": "e2", "source_type": "text_block", "text": "b" * 400},
        {"evidence_id": "e3", "source_type": "text_block", "text": "c" * 400},
    ]

    pack = build_evidence_pack("field", "field query", rows, ContextBudget(max_text_tokens=210, max_evidence_items=2))

    assert pack.evidence_ids == ["e1", "e2"]
    assert pack.estimated_text_tokens <= 210


def test_evidence_pack_separates_tables():
    rows = [
        {"evidence_id": "t1", "source_type": "table_row", "markdown": "| Revenue | 100 |"},
        {"evidence_id": "x1", "source_type": "text_block", "text": "Revenue was 100"},
    ]

    pack = build_evidence_pack("revenue", "revenue", rows, ContextBudget())

    assert [item["evidence_id"] for item in pack.tables] == ["t1"]
    assert [item["evidence_id"] for item in pack.text_snippets] == ["x1"]


def test_evidence_pack_truncates_oversized_single_chunk_instead_of_dropping_it():
    pack = build_evidence_pack(
        "documentName",
        "Document Name",
        [{"evidence_id": "doc-1", "source_type": "page", "text": "A" * 20_000}],
        ContextBudget(max_text_tokens=1000, max_evidence_items=3),
    )

    assert pack.evidence_ids == ["doc-1"]
    assert pack.estimated_text_tokens <= 1000
    assert len(pack.text_snippets[0]["text"]) <= 4000


def test_field_extractor_returns_table_rows_for_array_schema():
    pack = build_evidence_pack(
        "rows",
        "rows",
        [
            {
                "evidence_id": "e1",
                "source_type": "table_row",
                "text": "Revenue | 100",
                "metadata_json": {"rows": [{"item": "Revenue", "amount": "100"}]},
            }
        ],
        ContextBudget(),
    )

    candidates = FieldExtractor().extract("rows", {"type": "array"}, pack)

    assert candidates[0].value == [{"item": "Revenue", "amount": "100"}]
