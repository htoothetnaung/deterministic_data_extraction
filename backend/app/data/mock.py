"""In-memory data store with rich mock seed data.

This is a PLACEHOLDER persistence layer. Swap for a real database
(Postgres, SQLite, etc.) by replacing the ``store`` object with a repository
pattern backed by your ORM of choice (SQLAlchemy / SQLModel / Tortoise).
"""
from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models.document import (
    DocumentMetadata,
    DocumentSource,
    DocumentStatus,
    DocumentType,
)
from app.models.field import EditableExtractionField, FieldType


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _utc(**kw) -> datetime:
    return datetime.now(timezone.utc).replace(**kw) if kw else datetime.now(timezone.utc)


class _Store:
    def __init__(self) -> None:
        self.documents: dict[str, DocumentMetadata] = {}
        self.ocr_results: dict[str, Any] = {}
        # document_id -> { field_key: value } seed values for mock extraction
        self.seed_field_values: dict[str, dict[str, Any]] = {}
        self.templates: dict[str, Any] = {}
        self.batches: dict[str, Any] = {}
        self.benchmarks: dict[str, Any] = {}
        self._run_counter = itertools.count(1)

    def seed(self) -> None:
        """Populate the store with mock data. Called AFTER ``store`` is bound
        at module level so service modules can import ``store`` safely."""
        self._seed()

    # ---- id helpers -----------------------------------------------------
    def gen_id(self, prefix: str) -> str:
        return _id(prefix)

    def gen_run_id(self) -> str:
        n = next(self._run_counter)
        return f"RUN-{datetime.now(timezone.utc).strftime('%Y')}-{n:04d}"

    # ---- seed -----------------------------------------------------------
    def _seed(self) -> None:
        # ---- Corporate documents (placeholder DB) ----
        corp_docs = [
            ("Concrete Cube Test Report — Mix A", DocumentType.REPORT, "construction-reports", 8),
            ("Concrete Cube Test Report — Mix B", DocumentType.REPORT, "construction-reports", 8),
            ("Compressive Strength Report — Column C7", DocumentType.REPORT, "construction-reports", 6),
            ("Invoice INV-2024-0871", DocumentType.INVOICE, "finance", 2),
            ("Invoice INV-2024-0872", DocumentType.INVOICE, "finance", 2),
            ("Purchase Order PO-5523", DocumentType.OTHER, "procurement", 3),
            ("Subcontractor Agreement — Phase 2", DocumentType.CONTRACT, "legal", 14),
            ("Material Delivery Form — Cement", DocumentType.FORM, "logistics", 1),
        ]
        for i, (name, dtype, coll, pages) in enumerate(corp_docs):
            did = f"doc-corp-{i+1:03d}"
            self.documents[did] = DocumentMetadata(
                id=did,
                name=name,
                type=dtype,
                source=DocumentSource.CORPORATE_DB,
                mime_type="application/pdf",
                size_bytes=240_000 + i * 12_000,
                page_count=pages,
                status=DocumentStatus.UPLOADED,
                collection=coll,
                tags=[coll, dtype.value],
                uploaded_at=_utc(),
                confidence=0.0,
            )

        # ---- Uploaded (reviewed) sample document ----
        sample_id = "doc-upl-001"
        self.documents[sample_id] = DocumentMetadata(
            id=sample_id,
            name="Concrete_2s_sample.pdf",
            type=DocumentType.REPORT,
            source=DocumentSource.UPLOAD,
            mime_type="application/pdf",
            size_bytes=318_204,
            page_count=2,
            status=DocumentStatus.OCR_DONE,
            tags=["construction-reports", "report"],
            uploaded_at=_utc(),
            confidence=0.86,
        )


store = _Store()
store.seed()
