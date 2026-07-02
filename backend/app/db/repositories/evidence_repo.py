"""Evidence repository with weighted PostgreSQL FTS + pgvector search."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvidenceEmbeddingModel, EvidenceItemModel
from app.db.repositories.base import BaseRepository


HYBRID_SEARCH_SQL = text(
    """
    WITH fts AS (
        SELECT evidence_id, ts_rank(tsv_search, plainto_tsquery('english', :query)) AS score
        FROM evidence_items
        WHERE tsv_search @@ plainto_tsquery('english', :query)
          AND case_id = :case_id
          AND (CAST(:source_type_filter AS text) IS NULL OR source_type = CAST(:source_type_filter AS text))
    ),
    vector_scores AS (
            SELECT emb.evidence_id, 1 - (emb.embedding <=> CAST(:query_embedding AS vector)) AS score
        FROM evidence_embeddings emb
        JOIN evidence_items e2 ON e2.evidence_id = emb.evidence_id
        WHERE e2.case_id = :case_id
          AND (CAST(:source_type_filter AS text) IS NULL OR e2.source_type = CAST(:source_type_filter AS text))
        ORDER BY emb.embedding <=> CAST(:query_embedding AS vector)
        LIMIT :vec_limit
    )
    SELECT
        COALESCE(fts.evidence_id, v.evidence_id) AS evidence_id,
        COALESCE(fts.score, 0.0) * :fts_weight + COALESCE(v.score, 0.0) * :vec_weight AS hybrid_score,
        e.source_type,
        e.text,
        e.markdown,
        e.page_number,
        e.document_id,
        e.bbox,
        e.confidence,
        e.metadata_json
    FROM evidence_items e
    LEFT JOIN fts ON e.evidence_id = fts.evidence_id
    LEFT JOIN vector_scores v ON e.evidence_id = v.evidence_id
    WHERE (fts.evidence_id IS NOT NULL OR v.evidence_id IS NOT NULL)
      AND e.case_id = :case_id
      AND (CAST(:source_type_filter AS text) IS NULL OR e.source_type = CAST(:source_type_filter AS text))
    ORDER BY hybrid_score DESC
    LIMIT :top_k
    """
)

FTS_ONLY_SQL = text(
    """
    SELECT
        e.evidence_id,
        ts_rank(tsv_search, plainto_tsquery('english', :query)) AS score,
        e.source_type,
        e.text,
        e.markdown,
        e.page_number,
        e.document_id,
        e.bbox,
        e.confidence,
        e.metadata_json
    FROM evidence_items e
    WHERE e.tsv_search @@ plainto_tsquery('english', :query)
      AND e.case_id = :case_id
      AND (CAST(:source_type_filter AS text) IS NULL OR e.source_type = CAST(:source_type_filter AS text))
    ORDER BY score DESC
    LIMIT :top_k
    """
)

KEYWORD_SEARCH_SQL = text(
    """
    SELECT
        e.evidence_id,
        e.source_type,
        e.text,
        e.markdown,
        e.page_number,
        e.document_id,
        e.bbox,
        e.confidence,
        e.metadata_json
    FROM evidence_items e
    WHERE e.case_id = :case_id
      AND (CAST(:source_type_filter AS text) IS NULL OR e.source_type = CAST(:source_type_filter AS text))
      AND (
          to_tsvector('english', coalesce(e.text, '') || ' ' || coalesce(e.markdown, '')) @@ plainto_tsquery('english', :query)
          OR coalesce(e.text, e.markdown, '') ILIKE :like_query
      )
    ORDER BY e.created_at DESC
    LIMIT :top_k
    """
)

UPDATE_TSV_SQL = text(
    """
    UPDATE evidence_items
    SET tsv_search = to_tsvector('english', coalesce(text, '') || ' ' || coalesce(markdown, ''))
    WHERE evidence_id = :evidence_id
    """
)


class EvidenceRepository(BaseRepository[EvidenceItemModel]):
    """Async repository for evidence items with weighted FTS/vector search."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, EvidenceItemModel)

    async def create(
        self,
        case_id: str,
        document_id: str,
        page_number: int,
        source_type: str = "text_block",
        text: str | None = None,
        markdown: str | None = None,
        bbox: dict[str, Any] | list[float] | None = None,
        confidence: float | None = None,
        metadata_json: dict[str, Any] | None = None,
        page_id: str | None = None,
    ) -> EvidenceItemModel:
        item = EvidenceItemModel(
            case_id=case_id,
            document_id=document_id,
            page_id=page_id,
            page_number=page_number,
            source_type=source_type,
            text=text,
            markdown=markdown,
            bbox=bbox,
            confidence=confidence,
            metadata_json=metadata_json or {},
        )
        await self.add(item)
        await self.refresh_search_vector(item.evidence_id)
        return item

    async def create_from_clean_evidence(
        self,
        case_id: str,
        document_id: str,
        page_id: str | None,
        item: dict[str, Any],
    ) -> EvidenceItemModel:
        """Create an evidence item from evidence_cleaner.py output."""
        rows = item.get("rows")
        metadata = {
            "risk": item.get("risk"),
            "warnings": item.get("warnings", []),
            "source_url": item.get("source_url") or item.get("url"),
            "columns": item.get("columns", []),
            "rows": rows if isinstance(rows, list) else [],
        }
        source_type = str(item.get("type") or "text_block")
        if source_type == "table":
            source_type = "table_row" if rows else "table_cell"
        return await self.create(
            case_id=case_id,
            document_id=document_id,
            page_number=int(item.get("page") or 1),
            source_type=source_type,
            text=item.get("text") or item.get("text_preview"),
            markdown=item.get("markdown"),
            bbox=item.get("bbox"),
            confidence=item.get("confidence"),
            metadata_json=metadata,
            page_id=page_id,
        )

    async def create_from_chunk(
        self,
        case_id: str,
        document_id: str,
        chunk: dict[str, Any],
        page_id: str | None = None,
    ) -> EvidenceItemModel:
        """Create an evidence item from a chunker.Chunk.to_dict() payload."""
        chunk_type = str(chunk.get("type") or chunk.get("chunk_type") or "text")
        source_type = _source_type_for_chunk(chunk_type, chunk.get("rows"))
        metadata = {
            "strategy": chunk.get("strategy"),
            "risk": chunk.get("risk"),
            "warnings": chunk.get("warnings", []),
            "source_url": chunk.get("source_url"),
            "columns": chunk.get("columns", []),
            "rows": chunk.get("rows", []) if isinstance(chunk.get("rows"), list) else [],
            "table_index": chunk.get("table_index"),
            "row_index": chunk.get("row_index"),
            "header": chunk.get("header"),
            "token_count": chunk.get("token_count"),
            "chunk_id": chunk.get("id"),
            "metadata": chunk.get("metadata", {}),
        }
        return await self.create(
            case_id=case_id,
            document_id=document_id,
            page_number=int(chunk.get("page") or 1),
            source_type=source_type,
            text=chunk.get("text") or chunk.get("text_preview"),
            markdown=chunk.get("markdown"),
            bbox=chunk.get("bbox"),
            confidence=chunk.get("confidence"),
            metadata_json=metadata,
            page_id=page_id,
        )

    async def refresh_search_vector(self, evidence_id: str) -> None:
        await self.session.execute(UPDATE_TSV_SQL, {"evidence_id": evidence_id})
        await self.session.flush()

    async def hybrid_search(
        self,
        case_id: str,
        query: str,
        query_embedding: list[float] | None = None,
        top_k: int = 10,
        fts_weight: float = 0.4,
        vec_weight: float = 0.6,
        vec_limit: int = 50,
        source_type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Weighted PostgreSQL FTS + pgvector search with fallbacks."""
        if query_embedding is not None:
            vec_literal = str(query_embedding)
            params = {
                "query": query,
                "query_embedding": vec_literal,
                "case_id": case_id,
                "top_k": top_k,
                "fts_weight": fts_weight,
                "vec_weight": vec_weight,
                "vec_limit": vec_limit,
                "source_type_filter": source_type_filter,
            }
            result = await self.session.execute(HYBRID_SEARCH_SQL, params)
        else:
            params = {
                "query": query,
                "case_id": case_id,
                "top_k": top_k,
                "source_type_filter": source_type_filter,
            }
            result = await self.session.execute(FTS_ONLY_SQL, params)

        rows = [dict(row) for row in result.mappings()]
        if rows:
            return rows[:top_k]
        return await self.keyword_search(
            case_id=case_id,
            query=query,
            top_k=top_k,
            source_type_filter=source_type_filter,
        )

    async def keyword_search(
        self,
        case_id: str,
        query: str,
        top_k: int = 10,
        source_type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Simple FTS/ILIKE fallback when embeddings are unavailable."""
        params = {
            "case_id": case_id,
            "query": query,
            "like_query": f"%{query}%",
            "top_k": top_k,
            "source_type_filter": source_type_filter,
        }
        result = await self.session.execute(KEYWORD_SEARCH_SQL, params)
        return [dict(row) for row in result.mappings()]

    async def list_by_case(self, case_id: str) -> list[EvidenceItemModel]:
        return await self.list(case_id=case_id)

    async def list_by_document(self, document_id: str) -> list[EvidenceItemModel]:
        return await self.list(document_id=document_id)

    async def set_embedding(self, evidence_id: str, embedding: list[float]) -> EvidenceEmbeddingModel:
        """Attach or update a pgvector embedding for an evidence item."""
        stmt = select(EvidenceEmbeddingModel).where(EvidenceEmbeddingModel.evidence_id == evidence_id)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.embedding = embedding
            await self.session.flush()
            return existing
        emb = EvidenceEmbeddingModel(evidence_id=evidence_id, embedding=embedding)
        self.session.add(emb)
        await self.session.flush()
        return emb

    async def set_embedding_api(self, evidence_id: str, embedding_api: list[float]) -> EvidenceEmbeddingModel:
        """Attach or update the API-provider (Gemini, 3072-d) embedding for an evidence item."""
        stmt = select(EvidenceEmbeddingModel).where(EvidenceEmbeddingModel.evidence_id == evidence_id)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.embedding_api = embedding_api
            await self.session.flush()
            return existing
        emb = EvidenceEmbeddingModel(evidence_id=evidence_id, embedding=[0.0] * 768, embedding_api=embedding_api)
        self.session.add(emb)
        await self.session.flush()
        return emb

    async def delete_by_document(self, document_id: str) -> int:
        """Delete all evidence items for a document (cascades embeddings). Returns count."""
        from sqlalchemy import delete as sa_delete

        stmt = sa_delete(EvidenceItemModel).where(EvidenceItemModel.document_id == document_id)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(result.rowcount or 0)


def _source_type_for_chunk(chunk_type: str, rows: Any) -> str:
    """Map a chunk type to the evidence_items.source_type vocabulary."""
    normalized = (chunk_type or "").lower()
    if normalized == "table_row":
        return "table_row"
    if normalized == "table":
        return "table_row" if rows else "table_cell"
    if normalized in {"page", "document"}:
        return "page"
    if normalized in {"image", "figure", "chart"}:
        return "image_region"
    if normalized in {"sliding_window"}:
        return "text_block"
    return "text_block"
