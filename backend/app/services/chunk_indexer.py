"""Index chunker output into the pgvector/FTS evidence store.

Turns a list of Chunk objects (from app.services.chunker) into evidence_items
rows plus their embeddings so that hybrid retrieval (Postgres FTS + pgvector)
can run against them. Local 768-d embeddings are always generated for the
cost_effective tier; 3072-d Gemini embeddings are generated lazily for the
agentic tier when the google-genai SDK and an API key are available.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.evidence_repo import EvidenceRepository
from app.services import embedding
from app.services.chunker import Chunk


@dataclass
class IndexStats:
    document_id: str
    chunks_indexed: int
    local_embeddings: int
    api_embeddings: int
    skipped_empty: int
    strategy: str
    gemini_available: bool


async def index_chunks(
    session: AsyncSession,
    case_id: str,
    document_id: str,
    chunks: list[Chunk],
    *,
    embed_local: bool = True,
    embed_api: bool = False,
    replace_existing: bool = True,
    max_embed_chars: int = 8000,
) -> IndexStats:
    """Persist chunks as evidence_items + embeddings for one document."""
    repo = EvidenceRepository(session)
    if replace_existing:
        await repo.delete_by_document(document_id)

    created: list[tuple[str, str]] = []
    skipped_empty = 0
    strategy = chunks[0].strategy if chunks else "block"

    for chunk in chunks:
        text = (chunk.text or "").strip()
        if not text:
            skipped_empty += 1
            continue
        payload = chunk.to_dict(include_text=True)
        item = await repo.create_from_chunk(
            case_id=case_id,
            document_id=document_id,
            chunk=payload,
        )
        created.append((item.evidence_id, text[:max_embed_chars]))

    local_embeddings = 0
    api_embeddings = 0

    if embed_local and created:
        local_embeddings = await _embed_and_store(
            repo,
            created,
            embed_fn=embedding.embed_texts,
            setter=lambda r, eid, vec: r.set_embedding(eid, vec),
            refresh_tsv=True,
        )

    gemini_available = embedding.is_gemini_embeddings_available()
    if embed_api and gemini_available and created:
        api_embeddings = await _embed_and_store(
            repo,
            created,
            embed_fn=embedding.embed_texts_gemini,
            setter=lambda r, eid, vec: r.set_embedding_api(eid, vec),
            refresh_tsv=False,
        )

    await session.commit()

    return IndexStats(
        document_id=document_id,
        chunks_indexed=len(created),
        local_embeddings=local_embeddings,
        api_embeddings=api_embeddings,
        skipped_empty=skipped_empty,
        strategy=strategy,
        gemini_available=gemini_available,
    )


async def index_chunk_payloads(
    session: AsyncSession,
    case_id: str,
    document_id: str,
    chunk_dicts: list[dict[str, Any]],
    **kwargs: Any,
) -> IndexStats:
    """Index chunks supplied as plain dicts (Chunk.to_dict() output)."""
    chunks = [_dict_to_chunk(d) for d in chunk_dicts]
    chunks = [c for c in chunks if c is not None]
    return await index_chunks(session, case_id, document_id, chunks, **kwargs)


async def _embed_and_store(
    repo: EvidenceRepository,
    items: list[tuple[str, str]],
    *,
    embed_fn,
    setter,
    refresh_tsv: bool,
) -> int:
    texts = [text for _, text in items]
    try:
        vectors = embed_fn(texts)
    except Exception:
        return 0
    if not vectors:
        return 0
    stored = 0
    for (evidence_id, _text), vector in zip(items, vectors):
        if not vector:
            continue
        await setter(repo, evidence_id, vector)
        if refresh_tsv:
            await repo.refresh_search_vector(evidence_id)
        stored += 1
    return stored


def _dict_to_chunk(payload: dict[str, Any]) -> Chunk | None:
    from app.services.chunker import Chunk as _Chunk

    text = str(payload.get("text") or payload.get("text_preview") or "").strip()
    if not text:
        return None
    return _Chunk(
        chunk_id=str(payload.get("id") or payload.get("chunk_id") or ""),
        page=int(payload.get("page") or 1),
        chunk_type=str(payload.get("type") or payload.get("chunk_type") or "text"),
        text=text,
        bbox=payload.get("bbox") if isinstance(payload.get("bbox"), dict) else None,
        confidence=payload.get("confidence") if isinstance(payload.get("confidence"), (int, float)) else None,
        risk=str(payload.get("risk") or "normal"),
        warnings=list(payload.get("warnings") or []),
        source_url=payload.get("source_url"),
        columns=list(payload.get("columns")) if isinstance(payload.get("columns"), list) else None,
        rows=list(payload.get("rows")) if isinstance(payload.get("rows"), list) else None,
        table_index=payload.get("table_index"),
        row_index=payload.get("row_index"),
        header=list(payload.get("header")) if isinstance(payload.get("header"), list) else None,
        token_count=payload.get("token_count"),
        strategy=str(payload.get("strategy") or "block"),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )
