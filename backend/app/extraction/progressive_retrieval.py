from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.db.repositories.evidence_repo import EvidenceRepository
from app.extraction.evidence_pack import EvidencePack, build_evidence_pack
from app.extraction.planner import FieldRetrievalPlan
from app.services.embedding import embed_text
import logging
logger = logging.getLogger(__name__)


_WEIGHTED_FTS_VECTOR = "weighted_fts_vector"
_FTS_ONLY = "fts_only"
_FALLBACK = "fts_fallback"
_UNKNOWN = "unknown"


@dataclass
class RetrievalStats:
    mode: str = _UNKNOWN
    total_dense_hits: int = 0
    total_sparse_hits: int = 0
    field_modes: list[str] = field(default_factory=list)

    def record(self, row_mode: str, dense_hits: int, sparse_hits: int) -> None:
        self.total_dense_hits += dense_hits
        self.total_sparse_hits += sparse_hits
        self.field_modes.append(row_mode)
        if row_mode == _WEIGHTED_FTS_VECTOR and self.mode != _WEIGHTED_FTS_VECTOR:
            self.mode = _WEIGHTED_FTS_VECTOR
        elif row_mode == _FTS_ONLY and self.mode == _UNKNOWN:
            self.mode = _FTS_ONLY
        elif row_mode == _FALLBACK and self.mode == _UNKNOWN:
            self.mode = _FALLBACK

    @property
    def dense_hits(self) -> int:
        return self.total_dense_hits

    @property
    def sparse_hits(self) -> int:
        return self.total_sparse_hits


class ProgressiveRetriever:
    def __init__(self, evidence_repo: EvidenceRepository, *, use_api_embeddings: bool = False) -> None:
        self.evidence_repo = evidence_repo
        self.use_api_embeddings = use_api_embeddings
        self.retrieval_stats = RetrievalStats()

    async def retrieve(self, case_id: str, plan: FieldRetrievalPlan, attempt: int = 1) -> EvidencePack:
        top_k = 3 if attempt == 1 else 8 if attempt == 2 else plan.budget.max_evidence_items
        source_filter = plan.preferred_source_types[0] if attempt == 1 and plan.preferred_source_types else None
        query_embedding: list[float] | None = None
        mode = _FTS_ONLY
        try:
            query_embedding = await asyncio.to_thread(embed_text, plan.query)
            logger.debug("progressive_retrieval: embed_text field=%s attempt=%d", plan.field_path, attempt)
            mode = _WEIGHTED_FTS_VECTOR
        except Exception as e:
            logger.warning("progressive_retrieval: embed_text field=%s attempt=%d failed=%s", plan.field_path, attempt, e)
            pass
            
        rows = await self.evidence_repo.hybrid_search(
            case_id=case_id,
            query=plan.query,
            query_embedding=query_embedding,
            top_k=top_k,
            source_type_filter=source_filter,
        )
        logger.debug("progressive_retrieval: search field=%s attempt=%d top_k=%d rows=%d query=%s", plan.field_path, attempt, top_k, len(rows), plan.query[:80])
        if not rows and source_filter:
            rows = await self.evidence_repo.hybrid_search(
                case_id=case_id,
                query=plan.query,
                query_embedding=query_embedding,
                top_k=top_k,
            )
            logger.debug("progressive_retrieval: fallback_no_filter field=%s attempt=%d rows=%d", plan.field_path, attempt, len(rows))
        
        # Filter out image evidence in python if the field plan does not want images
        if not _plan_wants_images(plan):
            rows = [
                row for row in rows 
                if str(row.get("source_type") or "").lower() != "image" 
                and "image evidence:" not in str(row.get("text") or row.get("markdown") or "").lower()
            ]

        mode = _FALLBACK if not rows else mode
        dense_hits = len(rows) if query_embedding and rows else 0
        sparse_hits = len(rows)
        self.retrieval_stats.record(mode, dense_hits, sparse_hits)
        logger.debug("progressive_retrieval: retrieve field=%s attempt=%d mode=%s pack_size=%d", plan.field_path, attempt, mode, len(rows))
        return build_evidence_pack(plan.field_path, plan.query, rows, plan.budget)


def _plan_wants_images(plan: FieldRetrievalPlan) -> bool:
    haystack = f"{plan.field_path} {plan.query}".lower()
    return any(token in haystack for token in ("image", "images", "figure", "figures", "chart", "charts", "visual", "visuals"))
