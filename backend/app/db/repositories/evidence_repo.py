"""Evidence repository with weighted PostgreSQL FTS + pgvector search."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvidenceEmbeddingModel, EvidenceItemModel
from app.db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


HYBRID_SEARCH_SQL = text(
    """
    WITH fts AS (
        SELECT 
            evidence_id, 
            ROW_NUMBER() OVER(ORDER BY ts_rank(tsv_search, plainto_tsquery('english', :query)) DESC) AS rank
        FROM evidence_items
        WHERE tsv_search @@ plainto_tsquery('english', :query)
          AND case_id = :case_id
          AND (CAST(:source_type_filter AS text) IS NULL OR source_type = CAST(:source_type_filter AS text))
        LIMIT :sparse_limit
    ),
    vector_scores AS (
        SELECT 
            emb.evidence_id,
            ROW_NUMBER() OVER(ORDER BY emb.embedding <=> CAST(:query_embedding AS vector) ASC) AS rank
        FROM evidence_embeddings emb
        JOIN evidence_items e2 ON e2.evidence_id = emb.evidence_id
        WHERE e2.case_id = :case_id
          AND (CAST(:source_type_filter AS text) IS NULL OR e2.source_type = CAST(:source_type_filter AS text))
        ORDER BY emb.embedding <=> CAST(:query_embedding AS vector)
        LIMIT :dense_limit
    )
    SELECT
        COALESCE(fts.evidence_id, v.evidence_id) AS evidence_id,
        COALESCE(1.0 / (:rrf_k + fts.rank), 0.0) + COALESCE(1.0 / (:rrf_k + v.rank), 0.0) AS hybrid_score,
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
    """Async repository for managing and querying document evidence chunks.

    This repository is the core data-access point for RAG (Retrieval-Augmented Generation) search.
    It supports creating evidence items (text paragraphs, markdown tables, image coordinates)
    from parsers/cleaners, updating PostgreSQL Full Text Search (FTS) indices, saving vector embeddings,
    and running hybrid dense/sparse search with multiple fallbacks.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository, binding it specifically to the EvidenceItemModel table."""
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
        """Create a new evidence item and trigger its search vector refresh.

        This represents a single parsed element (text block, table row, image element).
        After inserting the evidence item, it executes `refresh_search_vector` to rebuild
        the FTS tsvector column (`tsv_search`) dynamically so it is immediately searchable.
        """
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
        """Convert a cleaned layout chunk (from evidence_cleaner.py) into a database evidence item.

        Normalizes source types (e.g. mapping parsed tables to 'table_row' or images to 'image_region')
        and maps fields like bounding boxes and metadata into standard database formats.
        """
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
        elif source_type in {"image", "figure", "chart"}:
            source_type = "image_region"
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
        """Convert a text/table/layout chunk (from chunker.py) into a database evidence item.

        Resolves structural layout types (sliding window, page tables, figures) and stores
        indexing metadata such as token counts, strategy names, and parent chunk IDs.
        """
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
        """Trigger update of the tsvector FTS indexing column (`tsv_search`) for a specific evidence item.

        Concatenates plain text and markdown contents, runs PostgreSQL stemmers on it,
        and saves it to the index column.
        """
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
        dense_limit: int = 10,
        sparse_limit: int = 10,
        rrf_k: int = 60,
    ) -> list[dict[str, Any]]:
        """Perform a hybrid search over evidence items, falling back to simpler searches if needed.

        The retrieval workflow is:
        1. If a vector embedding is provided, run a Reciprocal Rank Fusion (RRF) search.
        2. If no vector embedding is provided, fall back to pure database FTS using `plainto_tsquery`.
        3. If hybrid/FTS returns empty results, run `keyword_search` (ILIKE substring query + FTS fallback)
           to ensure no document context is missed due to tokenization or out-of-vocabulary terms.
        """
        path = "unknown"
        if query_embedding is not None:
            logger.debug("evidence_repo: hybrid_search path=weighted_fts_vector top_k=%d filter=%s", top_k, source_type_filter)
            path = "weighted_fts_vector"
            vec_literal = str(query_embedding)
            params = {
                "query": query,
                "query_embedding": vec_literal,
                "case_id": case_id,
                "top_k": top_k,
                "dense_limit": dense_limit if dense_limit is not None else vec_limit,
                "sparse_limit": sparse_limit,
                "rrf_k": rrf_k,
                "source_type_filter": source_type_filter,
            }
            result = await self.session.execute(HYBRID_SEARCH_SQL, params)
        else:
            logger.debug("evidence_repo: hybrid_search path=fts_only top_k=%d filter=%s", top_k, source_type_filter)
            path = "fts_only"
            params = {
                "query": query,
                "case_id": case_id,
                "top_k": top_k,
                "source_type_filter": source_type_filter,
            }
            result = await self.session.execute(FTS_ONLY_SQL, params)

        rows = [dict(row) for row in result.mappings()]
        if rows:
            logger.debug("evidence_repo: hybrid_search path=%s rows=%d", path, len(rows))
            return rows[:top_k]
        logger.info("evidence_repo: hybrid_search path=%s empty, falling back to keyword", path)
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
        """Perform a fallback database text search.

        Executes an FTS matching check and a standard SQL `ILIKE "%query%"` substring matching check.
        Ensures robust retrieval of acronyms, exact IDs, or short text terms that vector encoders miss.
        """
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
        """Fetch all evidence items parsed under a specific case."""
        return await self.list(case_id=case_id)

    async def list_by_document(self, document_id: str) -> list[EvidenceItemModel]:
        """Fetch all evidence items parsed from a single document."""
        return await self.list(document_id=document_id)

    async def set_embedding(self, evidence_id: str, embedding: list[float]) -> EvidenceEmbeddingModel:
        """Attach or update the primary dense retrieval embedding (e.g. OpenAI 1536-d) for an evidence item.

        Inserts a row into the `evidence_embeddings` table, or updates the coordinates if a vector record
        already exists for the item. Flushes the session.
        """
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
        """Attach or update the secondary API provider embedding (e.g. Gemini 3072-d) for an evidence item.

        Used in advanced extraction runs that request vector search against alternative models.
        """
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
        """Delete all evidence items parsed from a document (which cascades and deletes their embeddings).

        Returns the count of deleted evidence items. Used when re-processing or removing a document
        to clean up index clutter.
        """
        from sqlalchemy import delete as sa_delete

        stmt = sa_delete(EvidenceItemModel).where(EvidenceItemModel.document_id == document_id)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(result.rowcount or 0)


def _source_type_for_chunk(chunk_type: str, rows: Any) -> str:
    """Map a generic parsing chunk type string to the specific database `evidence_items.source_type` vocabulary.

    Categorizes chunks (e.g. text paragraphs, table cells, image elements) so search queries can filter by type.
    """
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