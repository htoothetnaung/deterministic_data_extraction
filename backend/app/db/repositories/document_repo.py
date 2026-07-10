"""Document repository with file hash lookup and metadata helpers."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentModel, DocumentJobModel
from app.db.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[DocumentModel]):
    """Async CRUD and queue ingestion for document files in cases.

    Manages individual document files, checks for file-hash duplicates,
    tracks ingestion status, and enqueues parsing/indexing tasks.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository, binding it specifically to the DocumentModel table."""
        super().__init__(session, DocumentModel)

    async def create(
        self,
        case_id: str,
        filename: str,
        mime_type: str = "application/octet-stream",
        file_hash: str | None = None,
        storage_path: str | None = None,
        size_bytes: int = 0,
        user_metadata: dict[str, Any] | None = None,
        inferred_metadata: dict[str, Any] | None = None,
        document_id: str | None = None,
    ) -> DocumentModel:
        """Create a new document record.

        Initializes status to 'uploaded' and registers disk storage location, size,
        SHA256 hash, and initial metadata.
        """
        doc = DocumentModel(
            document_id=document_id,
            case_id=case_id,
            filename=filename,
            mime_type=mime_type,
            file_hash=file_hash,
            storage_path=storage_path,
            size_bytes=size_bytes,
            user_metadata=user_metadata or {},
            inferred_metadata=inferred_metadata or {},
        )
        return await self.add(doc)

    async def get_by_hash(self, file_hash: str) -> DocumentModel | None:
        """Retrieve a document by its SHA256 file hash.

        Used to prevent redundant processing by checking if the exact same document
        has already been uploaded to the platform.
        """
        stmt = select(DocumentModel).where(DocumentModel.file_hash == file_hash).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_case(self, case_id: str) -> list[DocumentModel]:
        """Fetch all documents belonging to a specific case folder."""
        return await self.list(case_id=case_id)

    async def update_parser_status(self, document_id: str, status: str, **extra: Any) -> DocumentModel | None:
        """Update the document's parser pipeline status and update attributes.

        Changes status (e.g. 'quick_parsed', 'deep_parsed', 'indexed', 'failed').
        Extra keyword arguments are applied dynamically if they match columns on the model
        (e.g., page_count, priority).
        """
        doc = await self.get(document_id)
        if doc is None:
            return None
        doc.parser_status = status
        for key, value in extra.items():
            if hasattr(doc, key):
                setattr(doc, key, value)
        await self.session.flush()
        return doc

    async def update_inferred_metadata(self, document_id: str, metadata: dict[str, Any]) -> DocumentModel | None:
        """Merge a dictionary of key-value pairs into the document's inferred_metadata column.

        Used by parser stages to store document features (like page boundaries or structural metadata)
        discovered during ingestion.
        """
        doc = await self.get(document_id)
        if doc is None:
            return None
        doc.inferred_metadata = {**doc.inferred_metadata, **metadata}
        await self.session.flush()
        return doc

    async def enqueue_job(self, document_id: str, task_type: str, priority: int = 0) -> DocumentJobModel:
        """Enqueue a background task (e.g., 'quick_parse', 'deep_parse', 'index') for the document.

        Inserts a record into the `document_jobs` table. The background polling thread
        (`app.services.worker.py`) picks up these jobs in FIFO order of priority.
        """
        job = DocumentJobModel(
            document_id=document_id,
            task_type=task_type,
            priority=priority,
        )
        self.session.add(job)
        await self.session.flush()
        return job
