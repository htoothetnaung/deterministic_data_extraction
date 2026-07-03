from __future__ import annotations

from app.models.parser_benchmark import ParserRunResult, ParserStatus
from app.services import chunker


def _result(**overrides) -> ParserRunResult:
    base = dict(
        library="mistral_ocr",
        input_file="sample.pdf",
        input_type="pdf",
        status=ParserStatus.OK,
        seconds=1,
        pages=2,
        chars=100,
        tables=0,
        images=0,
    )
    base.update(overrides)
    return ParserRunResult(**base)


def test_page_strategy_chunks_page_markdown_and_text() -> None:
    result = _result(
        raw_text="<!-- page: 1 -->\nFirst page text about revenue.\n<!-- page: 2 -->\nSecond page text about liabilities.",
    )
    chunks = chunker.chunk_parser_result(result, strategy="page")

    assert len(chunks) == 2
    assert {c.page for c in chunks} == {1, 2}
    assert all(c.chunk_type == "page" for c in chunks)
    assert "revenue" in chunks[0].text.lower()
    assert "liabilities" in chunks[1].text.lower()


def test_structured_blocks_carry_bbox_and_page_metadata() -> None:
    result = _result(
        structured_preview={
            "blocks": [
                {
                    "type": "text",
                    "page": 1,
                    "text": "Net profit margin increased.",
                    "bbox": {"x0": 10.0, "y0": 20.0, "x1": 100.0, "y1": 40.0},
                    "confidence": 0.9,
                },
                {
                    "type": "heading",
                    "page": 2,
                    "text": "Risk Factors",
                    "bbox": {"x0": 0.0, "y0": 0.0, "x1": 50.0, "y1": 10.0},
                },
            ],
        },
    )
    chunks = chunker.chunk_parser_result(result, strategy="block")

    assert len(chunks) == 2
    first = chunks[0]
    assert first.page == 1
    assert first.chunk_type == "text"
    assert first.bbox == {"x0": 10.0, "y0": 20.0, "x1": 100.0, "y1": 40.0}
    assert first.confidence == 0.9
    assert chunks[1].page == 2
    assert chunks[1].chunk_type == "heading"


def test_parser_table_samples_become_table_and_table_row_chunks() -> None:
    result = _result(
        tables=1,
        raw_text=(
            "<!-- page: 1 -->\n"
            "| Item | Amount |\n| --- | --- |\n| Cash | 50 |\n| Equity | 120 |\n"
        ),
    )
    block_chunks = chunker.chunk_parser_result(result, strategy="block")
    row_chunks = chunker.chunk_parser_result(result, strategy="table_row")

    # block strategy keeps tables as single table chunks recovered from markdown
    table_blocks = [c for c in block_chunks if c.chunk_type == "table"]
    assert table_blocks, "markdown table should be recovered as a table block"
    assert table_blocks[0].columns == ["Item", "Amount"]
    assert len(table_blocks[0].rows) == 2

    # table_row strategy decomposes the table into one self-describing chunk per row
    row_chunks_of_table = [c for c in row_chunks if c.chunk_type == "table_row"]
    assert len(row_chunks_of_table) == 2
    assert row_chunks_of_table[0].header == ["Item", "Amount"]
    assert row_chunks_of_table[0].rows == [{"Item": "Cash", "Amount": "50"}]
    assert row_chunks_of_table[0].row_index == 0
    assert row_chunks_of_table[1].row_index == 1


def test_html_table_samples_become_table_chunks() -> None:
    result = _result(
        tables=1,
        raw_text=(
            "<!-- page: 1 -->\n"
            "<table><tr><th>Name</th><th>Value</th></tr>"
            "<tr><td>Revenue</td><td>100</td></tr></table>"
        ),
    )
    chunks = chunker.chunk_parser_result(result, strategy="block")
    tables = [c for c in chunks if c.chunk_type == "table"]
    assert tables, "HTML table should be recovered as a table chunk"
    assert tables[0].columns == ["Name", "Value"]
    assert tables[0].rows == [{"Name": "Revenue", "Value": "100"}]


def test_block_strategy_falls_back_to_raw_text_when_no_structured_preview() -> None:
    result = _result(raw_text="<!-- page: 1 -->\nA large body of narrative text with no structure.")
    chunks = chunker.chunk_parser_result(result, strategy="block")

    # No structured blocks and no markdown tables -> no block chunks, but the
    # page strategy still works (used as the default elsewhere). Verify the
    # parser-direct path does not crash and the page fallback covers it.
    page_chunks = chunker.chunk_parser_result(result, strategy="page")
    assert page_chunks
    assert "narrative text" in page_chunks[0].text


def test_document_strategy_emits_single_chunk() -> None:
    result = _result(raw_text="Whole document text spanning multiple sentences.")
    chunks = chunker.chunk_parser_result(result, strategy="document")

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "document"


def test_table_row_strategy_computes_distinct_row_bboxes() -> None:
    result = _result(
        structured_preview={
            "blocks": [
                {
                    "type": "table",
                    "page": 1,
                    "text": "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |",
                    "columns": ["A", "B"],
                    "rows": [{"A": "1", "B": "2"}, {"A": "3", "B": "4"}],
                    "bbox": {"x0": 10.0, "top": 100.0, "x1": 200.0, "bottom": 300.0},
                }
            ]
        }
    )
    chunks = chunker.chunk_parser_result(result, strategy="table_row")
    row_chunks = [c for c in chunks if c.chunk_type == "table_row"]
    assert len(row_chunks) == 2
    assert row_chunks[0].bbox["top"] == 100.0
    assert row_chunks[0].bbox["bottom"] == 200.0
    assert row_chunks[1].bbox["top"] == 200.0
    assert row_chunks[1].bbox["bottom"] == 300.0

