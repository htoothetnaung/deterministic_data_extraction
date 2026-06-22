"""Chunker service.

PLACEHOLDER. Implement document chunking here.

Responsibilities:
  * Convert sentences / blocks into retrieval-ready chunks.
  * Support multiple strategies (page-by-page, fixed-size, semantic).

TODO: implement real chunking.
"""
from __future__ import annotations

from typing import Any, Literal

ChunkStrategy = Literal["page-by-page", "fixed-size", "semantic", "sliding-window"]


def chunk_document(
    blocks: list[dict[str, Any]],
    strategy: ChunkStrategy = "page-by-page",
    max_pages: int = 10,
    chunk_size: int = 512,
) -> list[dict[str, Any]]:
    """Chunk a document into retrieval units."""
    # TODO: implement real chunking.
    return [
        {
            "id": f"chunk-{i}",
            "strategy": strategy,
            "text": block.get("text", ""),
            "page": block.get("page", 1),
        }
        for i, block in enumerate(blocks)
    ]
