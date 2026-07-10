"""Case repository."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CaseModel, DocumentModel, ExtractionJobModel
from app.db.repositories.base import BaseRepository


class CaseRepository(BaseRepository[CaseModel]):
    """Async CRUD and progress tracking for extraction cases.

    A Case represents a logical group of documents (e.g. KYC documents or invoice sets)
    submitted together for structured schema-based data extraction.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository, binding it specifically to the CaseModel table."""
        super().__init__(session, CaseModel)

    async def create(self, title: str, user_id: str = "local", metadata_json: dict[str, Any] | None = None) -> CaseModel:
        """Create a new extraction case.

        Saves the title (uer-facing folder name) ansd initializes metadata.
        flushes the session to assign a database UUID.
        """
        case = CaseModel(
            title=title,
            user_id=user_id,
            metadata_json=metadata_json or {},
        )
        return await self.add(case)

    async def list_recent(self, limit: int = 50) -> list[CaseModel]:
        """List cases sorted by modification time (most recently active first).

        Typically used to populate lists on the dashboard views.
        """
        stmt = select(CaseModel).order_by(CaseModel.updated_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_progress(self, case_id: str) -> dict[str, Any]:
        """Compile a progress dashboard summary for a case.

        Calculates counts of documents grouped by their parser status,
        counts total chunked evidence items, and fetches the latest batch extraction job.
        Provides the data backend for tracking status in UI progress loaders.
        """
        doc_counts = await self._document_counts(case_id)
        ev_count = await self._evidence_count(case_id)
        job = await self._latest_job(case_id)
        return {
            "case_id": case_id,
            "documents": doc_counts,
            "evidence_items": ev_count,
            "extraction_job": job,
        }

    async def _document_counts(self, case_id: str) -> dict[str, int]:
        """Compute the count of documents in the case grouped by their parser state.

        Groups by `parser_status` (e.g., 'uploaded', 'quick_parsed', 'deep_parsed', 'indexed', 'failed')
        to let callers check how many files are currently processing.
        """
        stmt = (
            select(
                DocumentModel.parser_status,
                func.count(DocumentModel.document_id),
            )
            .where(DocumentModel.case_id == case_id)
            .group_by(DocumentModel.parser_status)
        )
        result = await self.session.execute(stmt)
        counts: dict[str, int] = {"total": 0}
        for status, cnt in result:
            counts[status] = cnt
            counts["total"] += cnt
        return counts

    async def _evidence_count(self, case_id: str) -> int:
        """Count the total number of text/table/image chunks indexed in the vector store for this case.

        Indicates the size of the retrieval context pool for RAG operations.
        """
        from app.db.models import EvidenceItemModel
        stmt = select(func.count(EvidenceItemModel.evidence_id)).where(EvidenceItemModel.case_id == case_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def _latest_job(self, case_id: str) -> dict[str, Any] | None:
        """Retrieve execution details for the most recent extraction job run under this case.

        Tells the frontend whether a case extraction run is currently 'pending', 'running', or 'completed'.
        """
        stmt = (
            select(ExtractionJobModel)
            .where(ExtractionJobModel.case_id == case_id)
            .order_by(ExtractionJobModel.started_at.desc().nulls_last())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        job = result.scalar_one_or_none()
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "status": job.status,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }
