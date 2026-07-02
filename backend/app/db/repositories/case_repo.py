"""Case repository."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CaseModel, DocumentModel, ExtractionJobModel
from app.db.repositories.base import BaseRepository


class CaseRepository(BaseRepository[CaseModel]):
    """Async CRUD for extraction cases."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, CaseModel)

    async def create(self, title: str, user_id: str = "local", metadata_json: dict[str, Any] | None = None) -> CaseModel:
        case = CaseModel(
            title=title,
            user_id=user_id,
            metadata_json=metadata_json or {},
        )
        return await self.add(case)

    async def list_recent(self, limit: int = 50) -> list[CaseModel]:
        stmt = select(CaseModel).order_by(CaseModel.updated_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_progress(self, case_id: str) -> dict[str, Any]:
        """Return aggregate progress counts for a case."""
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
        from app.db.models import EvidenceItemModel
        stmt = select(func.count(EvidenceItemModel.evidence_id)).where(EvidenceItemModel.case_id == case_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def _latest_job(self, case_id: str) -> dict[str, Any] | None:
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
