"""Simple Postgres-backed document job worker."""
from __future__ import annotations

import asyncio

from app.db.engine import create_engine, get_factory, is_db_configured
from app.db.repositories.job_repo import DocumentJobRepository
from app.services.production_pipeline import deep_parse_document, index_document_evidence, quick_parse_document


async def poll_document_jobs(interval: float = 2.0) -> None:
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
                if job.task_type == "quick_parse":
                    await quick_parse_document(session, job.document_id)
                elif job.task_type == "deep_parse":
                    await deep_parse_document(session, job.document_id)
                elif job.task_type == "index":
                    await index_document_evidence(session, job.document_id)
                await repo.complete(job.job_id)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                async with factory() as fail_session:
                    fail_repo = DocumentJobRepository(fail_session)
                    await fail_repo.fail(job.job_id, str(exc))
                    await fail_session.commit()


async def main() -> None:
    create_engine()
    await poll_document_jobs()


if __name__ == "__main__":
    asyncio.run(main())
