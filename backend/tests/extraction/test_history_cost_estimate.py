from types import SimpleNamespace

from app.api.endpoints.extraction_lab import _estimate_history_job_cost_usd


def test_history_cost_estimate_uses_result_stats_and_attempt_tokens():
    job = SimpleNamespace(case=SimpleNamespace(documents=[SimpleNamespace(page_count=99)]))
    result_row = SimpleNamespace(
        response_json={
            "stats": {"pages": 10, "chunk_tokens": 1000},
            "data": {"field": "x" * 400},
        }
    )
    attempts = [SimpleNamespace(input_tokens=2000, output_tokens=None)]

    estimated = _estimate_history_job_cost_usd(job, result_row, attempts)

    # OCR: 10 * $0.004, embeddings: 1000 * $0.02/M,
    # LLM input: 2000 * $0.25/M, output estimated from response JSON.
    assert estimated > 0.0405
    assert estimated < 0.041


def test_history_cost_estimate_falls_back_to_document_pages():
    job = SimpleNamespace(
        case=SimpleNamespace(
            documents=[
                SimpleNamespace(page_count=3),
                SimpleNamespace(page_count=2),
            ]
        )
    )

    estimated = _estimate_history_job_cost_usd(job, None, [])

    assert estimated == 0.02
