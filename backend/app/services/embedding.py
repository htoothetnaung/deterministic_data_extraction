"""Embedding generation service.

PLACEHOLDER. Implement embedding models here.

Responsibilities:
  * Embed chunks / fields for retrieval & deterministic matching.
  * Support local (sentence-transformers) or API-based models.

Suggested libraries:
  * ``sentence-transformers``
  * ``openai`` embeddings
  * z-ai-web-dev-sdk embeddings

TODO: implement real embeddings.
"""
from __future__ import annotations

from typing import Any


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a list of texts."""
    # TODO: implement real embedding generation.
    return [[0.0] * 8 for _ in texts]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]
