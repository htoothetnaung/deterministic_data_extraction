"""Cost tracker for API extraction telemetry.

Estimates model usage costs based on input and output token counts and pricing rates.
"""
from __future__ import annotations


def estimate_cost(input_tokens: int, output_tokens: int = 0, per_1k_tokens: float = 0.0) -> float:
    """Calculate the estimated USD API cost for an LLM invocation.

    Rounds to six decimal places for precise micro-billing reports.
    """
    return round(((input_tokens + output_tokens) / 1000.0) * per_1k_tokens, 6)
