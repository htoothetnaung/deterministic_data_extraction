from __future__ import annotations


def estimate_cost(input_tokens: int, output_tokens: int = 0, per_1k_tokens: float = 0.0) -> float:
    return round(((input_tokens + output_tokens) / 1000.0) * per_1k_tokens, 6)
