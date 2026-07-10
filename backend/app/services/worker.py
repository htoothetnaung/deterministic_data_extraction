"""Simple Postgres-backed document job worker thread.

This background service runs continuously to poll, claim, and execute document processing tasks
(quick metadata parsing, deep OCR parsing, and dense embedding vector indexing) enqueued in
the `document_jobs` table.
"""
from __future__ import annotations

import asyncio

from app.db.engine import create_engine, get_factory, is_db_configured
from app.db.repositories.job_repo import DocumentJobRepository
from app.services.production_pipeline import parse_and_index_document


async def poll_document_jobs(interval: float = 2.0) -> None:
    """Continuously poll the document queue for pending ingestion tasks.

    Workflow:
    1. Claims the next pending task from the database queue in a concurrency-safe manner
       (preventing multiple worker threads from picking up the same document).
    2. Invokes the appropriate stage of the production pipeline based on `job.task_type`:
       * 'parse_and_index': Executes unified parsing, layout cleaning, and vector indexing.
    3. If the stage completes successfully, marks the job status as 'completed'.
    4. If an exception occurs, rolls back the active transaction and marks the job status
       as 'failed', logging the raw error traceback.
    """
    if not is_db_configured():
        create_engine()
    factory = get_factory()
    if factory is None:
        return
    while True:
        async with factory() as session:
            repo = DocumentJobRepository(session)
            job = await repo.claim_next()
            if job is None:
                await session.commit()
                await asyncio.sleep(interval)
                continue
            try:
                if job.task_type in {"parse_and_index", "quick_parse", "deep_parse", "index"}:
                    await parse_and_index_document(session, job.document_id)
                await repo.complete(job.job_id)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                async with factory() as fail_session:
                    fail_repo = DocumentJobRepository(fail_session)
                    await fail_repo.fail(job.job_id, str(exc))
                    await fail_session.commit()


async def main() -> None:
    """Start the background worker polling thread.

    Initializes the database engine pool and runs the infinite polling loop.
    """
    create_engine()
    await poll_document_jobs()


if __name__ == "__main__":
    asyncio.run(main())
