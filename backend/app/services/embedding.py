"""Embedding generation service.

Three providers are supported, keyed to the extraction tiers:
  * openai (primary):      OpenAI text-embedding-3-small,        1536-dim
  * local   (fallback):    sentence-transformers all-mpnet-base-v2, 768-dim
  * gemini (agentic):      Google gemini-embedding-001,           3072-dim

``embed_texts`` (the primary entry point used by the chunk indexer, the
production pipeline, and the progressive retriever) tries OpenAI first and
falls back to the local model only when OpenAI is unavailable (no key or
network error). The local model guarantees offline dev still works, but its
768-dim vectors are NOT compatible with the 1536-dim OpenAI vectors already
stored in the same pgvector column — pick one provider per evidence index and
do not mix them.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.core.config import settings

OPENAI_EMBEDDING_DIM = 1536
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
LOCAL_EMBEDDING_DIM = 768
GEMINI_EMBEDDING_DIM = 3072
GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"


@lru_cache(maxsize=1)
def _local_model():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - depends on optional runtime install
        raise RuntimeError("sentence-transformers is required for DB-backed evidence indexing") from exc
    return SentenceTransformer(settings.embedding_model_name, device=settings.embedding_device)


def _openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def is_openai_embeddings_available() -> bool:
    if not _openai_api_key():
        return False
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


def embed_texts_openai(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Generate 1536-dim OpenAI embeddings (text-embedding-3-small).

    ``input_type`` should be ``"document"`` for corpus texts and ``"query"``
    for retrieval queries. Returns an empty list if the SDK is unavailable or
    no API key is set so callers can treat OpenAI as optional/lazy.
    """
    if not texts:
        return []
    api_key = _openai_api_key()
    if not api_key:
        return []
    try:
        from openai import OpenAI
    except ImportError:
        return []

    client = OpenAI(api_key=api_key)
    results: list[list[float]] = []
    batch_size = 64
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        try:
            response = client.embeddings.create(
                model=OPENAI_EMBEDDING_MODEL,
                input=batch,
                dimensions=OPENAI_EMBEDDING_DIM,
                encoding_format="float",
            )
        except Exception:
            return []
        for item in response.data:
            values = item.embedding
            if values:
                results.append(list(values))
    return results


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for non-empty text inputs.

    OpenAI text-embedding-3-small (1536-dim) is the primary provider; the local
    sentence-transformers model (768-dim) is the fallback when OpenAI is
    unavailable. Callers that store these vectors must use a column sized for
    the active provider (Vector(1536) for OpenAI, Vector(768) for local).
    """
    if not texts:
        return []
    if is_openai_embeddings_available():
        vectors = embed_texts_openai(texts, input_type="document")
        if vectors:
            return vectors
    # Local fallback (offline dev / no key).
    model = _local_model()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [vector.tolist() for vector in vectors]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


def embed_query_openai(query: str) -> list[float]:
    """Generate a 1536-dim OpenAI query embedding."""
    vectors = embed_texts_openai([query], input_type="query")
    return vectors[0] if vectors else []


def embed_texts_gemini(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Generate 3072-dim Gemini embeddings for non-empty text inputs.

    Uses the google-genai SDK and the configured GOOGLE_API_KEY. Returns an
    empty list if the SDK is unavailable or no API key is set so callers can
    treat the agentic embedding path as optional/lazy.
    """
    if not texts:
        return []
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return []
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return []

    client = genai.Client(api_key=api_key)
    results: list[list[float]] = []
    batch_size = 64
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        try:
            response = client.models.embed_content(
                model=GEMINI_EMBEDDING_MODEL,
                contents=batch,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=GEMINI_EMBEDDING_DIM,
                ),
            )
        except Exception:
            return []
        for embedding in getattr(response, "embeddings", None) or []:
            values = getattr(embedding, "values", None)
            if values:
                results.append(list(values))
    return results


def embed_query_gemini(query: str) -> list[float]:
    """Generate a 3072-dim Gemini query embedding (RETRIEVAL_QUERY task type)."""
    vectors = embed_texts_gemini([query], task_type="RETRIEVAL_QUERY")
    return vectors[0] if vectors else []


def is_gemini_embeddings_available() -> bool:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return False
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return True
