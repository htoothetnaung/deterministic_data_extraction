"""Job repositories for extraction and document queue."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExtractionJobModel, FieldResultModel, FieldCandidateModel, FieldAttemptModel, DocumentJobModel
from app.db.repositories.base import BaseRepository


# =====================================================================
#  Extraction Job Repository
# =====================================================================


class ExtractionJobRepository(BaseRepository[ExtractionJobModel]):
    """Async repository for logging batch extraction runs, field results, and LLM retry history.

    This repository handles logging the structural outputs of the extraction pipeline. It tracks:
    1. Overall job runs (ExtractionJobModel).
    2. Final resolved field values and validation statuses (FieldResultModel).
    3. Multi-attempt LLM reasoning history, token logs, and execution costs (FieldAttemptModel).
    4. Text candidates and bounding-box evidence matches discovered during extraction (FieldCandidateModel).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository, binding it specifically to the ExtractionJobModel table."""
        super().__init__(session, ExtractionJobModel)

    async def create_job(
        self,
        case_id: str,
        schema_id: str,
        schema_json: dict[str, Any] | None = None,
    ) -> ExtractionJobModel:
        """Initialize a new extraction job run.

        Sets the status to 'pending' and logs the case and target schema configuration.
        """
        job = ExtractionJobModel(
            case_id=case_id,
            schema_id=schema_id,
            schema_json=schema_json,
            status="pending",
            started_at=datetime.now(timezone.utc),
        )
        return await self.add(job)

    async def get_with_fields(self, job_id: str) -> ExtractionJobModel | None:
        """Fetch an extraction job by its ID.

        Used to load the job log along with its nested relationship collections.
        """
        stmt = select(ExtractionJobModel).where(ExtractionJobModel.job_id == job_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(self, job_id: str, status: str) -> ExtractionJobModel | None:
        """Update the progress status of an extraction job.

        Sets status (e.g., 'pending', 'running', 'completed', 'failed', 'needs_review').
        If the status transitions to a final completed/failed state, it updates completed_at.
        """
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
        """Log a resolved field value and its validation outputs.

        Saves the final extracted value (e.g. string, number, or boolean) for a schema field
        (defined by field_path), along with its confidence level and any regex or enum validation errors.
        """
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
        """Log an intermediate value candidate generated during a field extraction step.

        Logs candidates (e.g. individual dates found on different pages before the final date
        is selected) and maps them to their source evidence_ids (the text chunks they were parsed from).
        This provides database lineage for UI hover highlights.
        """
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
        """Log LLM execution telemetry for an extraction run attempt.

        Records the prompts, context sizes, tokens used, and API billing costs for each attempt
        to resolve a field. Crucial for developer telemetry and cost/accuracy auditing.
        """
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


# =====================================================================
#  Document Queue Repository
# =====================================================================


class DocumentJobRepository(BaseRepository[DocumentJobModel]):
    """Async queue for document processing tasks.

    Handles enqueuing, thread-safe claiming, completion, and failure logs for background jobs
    (such as quick metadata extraction, full text OCR parsing, and chunk embedding indexing).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository, binding it specifically to the DocumentJobModel table."""
        super().__init__(session, DocumentJobModel)

    async def enqueue(self, document_id: str, task_type: str, priority: int = 0) -> DocumentJobModel:
        """Add a new task (e.g. 'deep_parse' or 'index') to the document queue."""
        job = DocumentJobModel(document_id=document_id, task_type=task_type, priority=priority)
        return await self.add(job)

    async def claim_next(self, task_types: list[str] | None = None) -> DocumentJobModel | None:
        """Claim the next pending job in a concurrency-safe manner.

        Utilizes `with_for_update(skip_locked=True)` to implement a row-level locking queue.
        This prevents multiple background worker threads from claiming or running the same document
        jobs concurrently, supporting high-throughput ingestion.
        """
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
        """Mark a claimed document job as successfully finished."""
        job = await self.get(job_id)
        if job is None:
            return None
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return job

    async def fail(self, job_id: str, error: str) -> DocumentJobModel | None:
        """Mark a claimed document job as failed, logging the raw error trace."""
        job = await self.get(job_id)
        if job is None:
            return None
        job.status = "failed"
        job.error = error
        job.completed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return job

    async def pending_count(self, task_type: str | None = None) -> int:
        """Return the count of tasks currently waiting in the queue.

        Optionally filters by task type (e.g. to show 'indexing' queue backlogs).
        """
        filters: dict[str, Any] = {"status": "pending"}
        if task_type:
            filters["task_type"] = task_type
        return await self.count(**filters)
