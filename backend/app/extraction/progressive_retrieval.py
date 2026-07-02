from __future__ import annotations

from app.db.repositories.evidence_repo import EvidenceRepository
from app.extraction.evidence_pack import EvidencePack, build_evidence_pack
from app.extraction.planner import FieldRetrievalPlan
from app.services.embedding import embed_query_openai, embed_text, is_openai_embeddings_available


class ProgressiveRetriever:
    def __init__(self, evidence_repo: EvidenceRepository, *, use_api_embeddings: bool = False) -> None:
        self.evidence_repo = evidence_repo
        self.use_api_embeddings = use_api_embeddings

    async def retrieve(self, case_id: str, plan: FieldRetrievalPlan, attempt: int = 1) -> EvidencePack:
        top_k = 3 if attempt == 1 else 8 if attempt == 2 else plan.budget.max_evidence_items
        source_filter = plan.preferred_source_types[0] if attempt == 1 and plan.preferred_source_types else None
        try:
            # Cost-effective extraction must stay deterministic and offline.
            # Agentic runs can opt into provider embeddings.
            query_embedding = (
                embed_query_openai(plan.query)
                if self.use_api_embeddings and is_openai_embeddings_available()
                else embed_text(plan.query)
            )
        except Exception:
            query_embedding = None
        rows = await self.evidence_repo.hybrid_search(
            case_id=case_id,
            query=plan.query,
            query_embedding=query_embedding,
            top_k=top_k,
            source_type_filter=source_filter,
        )
        if not rows and source_filter:
            rows = await self.evidence_repo.hybrid_search(
                case_id=case_id,
                query=plan.query,
                query_embedding=query_embedding,
                top_k=top_k,
            )
        return build_evidence_pack(plan.field_path, plan.query, rows, plan.budget)
