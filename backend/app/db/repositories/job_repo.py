"""Job repositories for extraction and document queue."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExtractionJobModel, FieldResultModel, FieldCandidateModel, FieldAttemptModel, DocumentJobModel
from app.db.repositories.base import BaseRepository


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Extraction Job Repository
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class ExtractionJobRepository(BaseRepository[ExtractionJobModel]):
    """Async CRUD for extraction jobs and their field results."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ExtractionJobModel)

    async def create_job(
        self,
        case_id: str,
        schema_id: str,
        schema_json: dict[str, Any] | None = None,
    ) -> ExtractionJobModel:
        job = ExtractionJobModel(
            case_id=case_id,
            schema_id=schema_id,
            schema_json=schema_json,
            status="pending",
            started_at=datetime.now(timezone.utc),
        )
        return await self.add(job)

    async def get_with_fields(self, job_id: str) -> ExtractionJobModel | None:
        """Fetch job with eagerly loaded field results."""
        stmt = select(ExtractionJobModel).where(ExtractionJobModel.job_id == job_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(self, job_id: str, status: str) -> ExtractionJobModel | None:
        job = await self.get(job_id)
        if job is None:
            return None
        job.status = status
        if status in ("completed", "failed", "needs_review"):
            job.completed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return job

    async def add_field_result(
        self,
        job_id: str,
        field_path: str,
        value: Any = None,
        status: str = "missing",
        confidence: float = 0.0,
        validation_errors: list[str] | None = None,
    ) -> FieldResultModel:
        fr = FieldResultModel(
            job_id=job_id,
            field_path=field_path,
            value=value,
            status=status,
            confidence=confidence,
            validation_errors=validation_errors or [],
        )
        self.session.add(fr)
        await self.session.flush()
        return fr

    async def add_candidate(
        self,
        field_result_id: str,
        value: Any,
        confidence: float,
        evidence_ids: list[str] | None = None,
        extraction_method: str = "keyword_rule",
    ) -> FieldCandidateModel:
        cand = FieldCandidateModel(
            field_result_id=field_result_id,
            value=value,
            confidence=confidence,
            evidence_ids=evidence_ids or [],
            extraction_method=extraction_method,
        )
        self.session.add(cand)
        await self.session.flush()
        return cand

    async def add_attempt(
        self,
        field_result_id: str,
        attempt_number: int,
        evidence_pack: dict[str, Any] | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        model_used: str | None = None,
        cost: float | None = None,
        error: str | None = None,
    ) -> FieldAttemptModel:
        attempt = FieldAttemptModel(
            field_result_id=field_result_id,
            attempt_number=attempt_number,
            evidence_pack=evidence_pack or {},
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_used=model_used,
            cost=cost,
            error=error,
        )
        self.session.add(attempt)
        await self.session.flush()
        return attempt


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Document Queue Repository
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class DocumentJobRepository(BaseRepository[DocumentJobModel]):
    """Async queue for document processing tasks."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, DocumentJobModel)

    async def enqueue(self, document_id: str, task_type: str, priority: int = 0) -> DocumentJobModel:
        job = DocumentJobModel(document_id=document_id, task_type=task_type, priority=priority)
        return await self.add(job)

    async def claim_next(self, task_types: list[str] | None = None) -> DocumentJobModel | None:
        """Claim the next pending job (FIFO within priority)."""
        stmt = (
            select(DocumentJobModel)
            .where(DocumentJobModel.status == "pending")
            .order_by(DocumentJobModel.priority.desc(), DocumentJobModel.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if task_types:
            stmt = stmt.where(DocumentJobModel.task_type.in_(task_types))
        result = await self.session.execute(stmt)
        job = result.scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await self.session.flush()
        return job

    async def complete(self, job_id: str) -> DocumentJobModel | None:
        job = await self.get(job_id)
        if job is None:
            return None
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return job

    async def fail(self, job_id: str, error: str) -> DocumentJobModel | None:
        job = await self.get(job_id)
        if job is None:
            return None
        job.status = "failed"
        job.error = error
        job.completed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return job

    async def pending_count(self, task_type: str | None = None) -> int:
        filters: dict[str, Any] = {"status": "pending"}
        if task_type:
            filters["task_type"] = task_type
        return await self.count(**filters)
