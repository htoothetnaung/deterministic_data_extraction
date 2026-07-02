"""SQLAlchemy ORM models for the production extraction pipeline.

Maps to existing Pydantic models in app/models/extraction.py, schema.py,
document.py.  All tables use UUID primary keys generated at the application
layer so they stay compatible with the existing uuid-based id scheme.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.db.engine import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Case
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class CaseModel(Base):
    __tablename__ = "cases"

    case_id = mapped_column(String(50), primary_key=True, default=_new_id)
    user_id = mapped_column(String(100), nullable=False, default="local")
    title = mapped_column(String(500), nullable=False)
    status = mapped_column(String(30), nullable=False, default="open")
    metadata_json = mapped_column(JSONB, nullable=False, default=dict)

    created_at = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    documents = relationship("DocumentModel", back_populates="case", cascade="all, delete-orphan")
    extraction_jobs = relationship("ExtractionJobModel", back_populates="case", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Case {self.case_id} status={self.status}>"  # pragma: no cover


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Document
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class DocumentModel(Base):
    __tablename__ = "documents"

    document_id = mapped_column(String(50), primary_key=True, default=_new_id)
    case_id = mapped_column(String(50), ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False, index=True)
    filename = mapped_column(String(500), nullable=False)
    mime_type = mapped_column(String(100), nullable=False, default="application/octet-stream")

    file_hash = mapped_column(String(64), nullable=True, index=True)  # sha256
    storage_path = mapped_column(String(1000), nullable=True)
    page_count = mapped_column(Integer, nullable=False, default=0)

    user_metadata = mapped_column(JSONB, nullable=False, default=dict)
    inferred_metadata = mapped_column(JSONB, nullable=False, default=dict)

    parser_status = mapped_column(String(30), nullable=False, default="pending")  # pending | quick_parsed | parsed | indexed | failed
    parse_quality = mapped_column(String(20), nullable=True)
    priority = mapped_column(Integer, nullable=False, default=0)
    failure_info = mapped_column(JSONB, nullable=True)

    size_bytes = mapped_column(Integer, nullable=False, default=0)
    confidence = mapped_column(Float, nullable=True)

    created_at = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    case = relationship("CaseModel", back_populates="documents")
    pages = relationship("PageModel", back_populates="document", cascade="all, delete-orphan")
    evidence_items_rel = relationship("EvidenceItemModel", back_populates="document", cascade="all, delete-orphan")
    document_jobs = relationship("DocumentJobModel", back_populates="document", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Document {self.document_id} file={self.filename}>"  # pragma: no cover


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Page
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class PageModel(Base):
    __tablename__ = "pages"

    page_id = mapped_column(String(50), primary_key=True, default=_new_id)
    document_id = mapped_column(String(50), ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False, index=True)
    page_number = mapped_column(Integer, nullable=False)

    text = mapped_column(Text, nullable=False, default="")
    markdown = mapped_column(Text, nullable=True)
    image_path = mapped_column(String(1000), nullable=True)

    width = mapped_column(Integer, nullable=True)
    height = mapped_column(Integer, nullable=True)

    parse_quality = mapped_column(String(20), nullable=True)

    document = relationship("DocumentModel", back_populates="pages")
    evidence_items_rel = relationship("EvidenceItemModel", back_populates="page", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Page doc={self.document_id} p={self.page_number}>"  # pragma: no cover


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Evidence Item
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class EvidenceItemModel(Base):
    __tablename__ = "evidence_items"

    evidence_id = mapped_column(String(50), primary_key=True, default=_new_id)
    case_id = mapped_column(String(50), ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = mapped_column(String(50), ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False, index=True)
    page_id = mapped_column(String(50), ForeignKey("pages.page_id", ondelete="SET NULL"), nullable=True)
    page_number = mapped_column(Integer, nullable=False)

    source_type = mapped_column(String(30), nullable=False, default="text_block")  # text_block | table_cell | table_row | page | image_region
    text = mapped_column(Text, nullable=True)
    markdown = mapped_column(Text, nullable=True)
    bbox = mapped_column(JSONB, nullable=True)
    metadata_json = mapped_column(JSONB, nullable=False, default=dict)
    confidence = mapped_column(Float, nullable=True)

    tsv_search = mapped_column(TSVECTOR, nullable=True)

    created_at = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    document = relationship("DocumentModel", back_populates="evidence_items_rel")
    page = relationship("PageModel", back_populates="evidence_items_rel")
    embedding = relationship("EvidenceEmbeddingModel", back_populates="evidence_item", uselist=False, cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Evidence {self.evidence_id} doc={self.document_id} p={self.page_number}>"  # pragma: no cover


__idx_evidence_tsv = Index("idx_evidence_tsv", EvidenceItemModel.tsv_search, postgresql_using="gin")
__idx_evidence_case_type = Index("idx_evidence_case_type", EvidenceItemModel.case_id, EvidenceItemModel.source_type)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Evidence Embedding (pgvector)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class EvidenceEmbeddingModel(Base):
    __tablename__ = "evidence_embeddings"

    embedding_id = mapped_column(String(50), primary_key=True, default=_new_id)
    evidence_id = mapped_column(String(50), ForeignKey("evidence_items.evidence_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    # 1536-dim matches the primary OpenAI text-embedding-3-small provider.
    # NOTE: if you point this at an existing live Postgres, run
    #   ALTER TABLE evidence_embeddings ALTER COLUMN embedding TYPE vector(1536);
    # before starting the service.
    embedding = mapped_column(Vector(1536), nullable=False)
    embedding_api = mapped_column(Vector(3072), nullable=True)

    evidence_item = relationship("EvidenceItemModel", back_populates="embedding")

    def __repr__(self) -> str:
        return f"<Embedding evidence={self.evidence_id}>"  # pragma: no cover


__idx_embedding_hnsw = Index(
    "idx_embedding_hnsw",
    EvidenceEmbeddingModel.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
__idx_embedding_api_hnsw = Index(
    "idx_embedding_api_hnsw",
    EvidenceEmbeddingModel.embedding_api,
    postgresql_using="hnsw",
    postgresql_ops={"embedding_api": "vector_cosine_ops"},
)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Extraction Job
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class ExtractionJobModel(Base):
    __tablename__ = "extraction_jobs"

    job_id = mapped_column(String(50), primary_key=True, default=_new_id)
    case_id = mapped_column(String(50), ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False, index=True)
    schema_id = mapped_column(String(50), nullable=False)
    schema_json = mapped_column(JSONB, nullable=True)
    status = mapped_column(String(30), nullable=False, default="pending")  # pending | running | completed | needs_review | failed
    started_at = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at = mapped_column(DateTime(timezone=True), nullable=True)

    case = relationship("CaseModel", back_populates="extraction_jobs")
    field_results = relationship("FieldResultModel", back_populates="job", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<ExtractionJob {self.job_id} case={self.case_id} status={self.status}>"  # pragma: no cover


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Field Result
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class FieldResultModel(Base):
    __tablename__ = "field_results"

    field_result_id = mapped_column(String(50), primary_key=True, default=_new_id)
    job_id = mapped_column(String(50), ForeignKey("extraction_jobs.job_id", ondelete="CASCADE"), nullable=False, index=True)
    field_path = mapped_column(String(200), nullable=False)
    value = mapped_column(JSONB, nullable=True)
    status = mapped_column(String(30), nullable=False, default="missing")  # validated | missing | conflict | low_confidence | invalid | human_corrected
    confidence = mapped_column(Float, nullable=False, default=0.0)
    validation_errors = mapped_column(JSONB, nullable=False, default=list)
    attempt_count = mapped_column(Integer, nullable=False, default=0)

    job = relationship("ExtractionJobModel", back_populates="field_results")
    candidates = relationship("FieldCandidateModel", back_populates="field_result", cascade="all, delete-orphan")
    attempts = relationship("FieldAttemptModel", back_populates="field_result", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<FieldResult {self.field_path} status={self.status}>"  # pragma: no cover


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Field Candidate
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class FieldCandidateModel(Base):
    __tablename__ = "field_candidates"

    candidate_id = mapped_column(String(50), primary_key=True, default=_new_id)
    field_result_id = mapped_column(String(50), ForeignKey("field_results.field_result_id", ondelete="CASCADE"), nullable=False, index=True)
    value = mapped_column(JSONB, nullable=True)
    confidence = mapped_column(Float, nullable=False, default=0.0)
    evidence_ids = mapped_column(ARRAY(String), nullable=False, default=list)
    extraction_method = mapped_column(String(30), nullable=False, default="keyword_rule")

    field_result = relationship("FieldResultModel", back_populates="candidates")

    def __repr__(self) -> str:
        return f"<Candidate {self.candidate_id} conf={self.confidence}>"  # pragma: no cover


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Field Attempt (debug / retry history)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class FieldAttemptModel(Base):
    __tablename__ = "field_attempts"

    attempt_id = mapped_column(String(50), primary_key=True, default=_new_id)
    field_result_id = mapped_column(String(50), ForeignKey("field_results.field_result_id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number = mapped_column(Integer, nullable=False, default=1)

    evidence_pack = mapped_column(JSONB, nullable=False, default=dict)
    input_tokens = mapped_column(Integer, nullable=True)
    output_tokens = mapped_column(Integer, nullable=True)
    model_used = mapped_column(String(100), nullable=True)
    cost = mapped_column(Numeric(12, 6), nullable=True)
    error = mapped_column(Text, nullable=True)

    created_at = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    field_result = relationship("FieldResultModel", back_populates="attempts")

    def __repr__(self) -> str:
        return f"<Attempt {self.attempt_id} field={self.field_result_id} n={self.attempt_number}>"  # pragma: no cover


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#  Document Job (internal queue table)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


DOCJOB_TASK_TYPES = frozenset({"quick_parse", "deep_parse", "index", "extract_ready_fields"})
DOCJOB_STATUSES = frozenset({"pending", "running", "completed", "failed"})


class DocumentJobModel(Base):
    __tablename__ = "document_jobs"

    job_id = mapped_column(String(50), primary_key=True, default=_new_id)
    document_id = mapped_column(String(50), ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False, index=True)
    task_type = mapped_column(String(30), nullable=False)  # quick_parse | deep_parse | index | extract_ready_fields
    status = mapped_column(String(20), nullable=False, default="pending")  # pending | running | completed | failed
    priority = mapped_column(Integer, nullable=False, default=0)
    error = mapped_column(Text, nullable=True)

    created_at = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    started_at = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at = mapped_column(DateTime(timezone=True), nullable=True)

    document = relationship("DocumentModel", back_populates="document_jobs")

    def __repr__(self) -> str:
        return f"<DocJob {self.job_id} doc={self.document_id} task={self.task_type}>"  # pragma: no cover


__idx_docjob_status = Index("idx_docjob_status", DocumentJobModel.status, DocumentJobModel.priority)


class ExtractionResultModel(Base):
    __tablename__ = "extraction_results"

    run_id = mapped_column(String(50), primary_key=True)
    input_id = mapped_column(String(200), nullable=False, index=True)
    schema_name = mapped_column(String(200), nullable=False)
    response_json = mapped_column(JSONB, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<ExtractionResult {self.run_id} input={self.input_id}>"

