"""SQLAlchemy ORM models for the production extraction pipeline.

Maps to existing Pydantic models in app/models/extraction.py, schema.py,
document.py. All tables use UUID primary keys generated at the application
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
    """Return the current time in UTC timezone."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """Generate a unique 12-character hexadecimal ID string."""
    return uuid.uuid4().hex[:12]


# =====================================================================
#  Case Model
# =====================================================================


class CaseModel(Base):
    """ORM Model representing an extraction Case.

    A Case aggregates a bundle of document files (e.g., KYC files or invoice sets)
    subjected to data extraction against a structured property schema.
    """
    __tablename__ = "cases"

    case_id = mapped_column(String(50), primary_key=True, default=_new_id)
    user_id = mapped_column(String(100), nullable=False, default="local")
    title = mapped_column(String(500), nullable=False)
    status = mapped_column(String(30), nullable=False, default="open")
    metadata_json = mapped_column(JSONB, nullable=False, default=dict)
    settings = mapped_column(JSONB, nullable=True)

    created_at = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    # Relationships
    documents = relationship("DocumentModel", back_populates="case", cascade="all, delete-orphan")
    extraction_jobs = relationship("ExtractionJobModel", back_populates="case", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Case {self.case_id} status={self.status}>"  # pragma: no cover


# =====================================================================
#  Document Model
# =====================================================================


class DocumentModel(Base):
    """ORM Model representing an uploaded or corporate document file.

    Tracks ingestion state, page counts, file locations on disk, file-hashes, and overall processing quality.
    """
    __tablename__ = "documents"

    document_id = mapped_column(String(50), primary_key=True, default=_new_id)
    case_id = mapped_column(String(50), ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False, index=True)
    filename = mapped_column(String(500), nullable=False)
    mime_type = mapped_column(String(100), nullable=False, default="application/octet-stream")

    file_hash = mapped_column(String(64), nullable=True, index=True)  # SHA256 checksum
    storage_path = mapped_column(String(1000), nullable=True)
    page_count = mapped_column(Integer, nullable=False, default=0)

    user_metadata = mapped_column(JSONB, nullable=False, default=dict)
    inferred_metadata = mapped_column(JSONB, nullable=False, default=dict)

    # Parser statuses: 'pending', 'quick_parsed', 'deep_parsed', 'indexed', 'failed'
    parser_status = mapped_column(String(30), nullable=False, default="pending")
    parse_quality = mapped_column(String(20), nullable=True)
    priority = mapped_column(Integer, nullable=False, default=0)
    failure_info = mapped_column(JSONB, nullable=True)

    size_bytes = mapped_column(Integer, nullable=False, default=0)
    confidence = mapped_column(Float, nullable=True)

    created_at = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    # Relationships
    case = relationship("CaseModel", back_populates="documents")
    pages = relationship("PageModel", back_populates="document", cascade="all, delete-orphan")
    evidence_items_rel = relationship("EvidenceItemModel", back_populates="document", cascade="all, delete-orphan")
    document_jobs = relationship("DocumentJobModel", back_populates="document", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Document {self.document_id} file={self.filename}>"  # pragma: no cover


# =====================================================================
#  Page Model
# =====================================================================


class PageModel(Base):
    """ORM Model representing a single page of a document.

    Stores page-level layout, text, markdown transcripts, and rasterized page image paths.
    """
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

    # Relationships
    document = relationship("DocumentModel", back_populates="pages")
    evidence_items_rel = relationship("EvidenceItemModel", back_populates="page", cascade="all, delete-orphan")  # maps page to its chunks

    def __repr__(self) -> str:
        return f"<Page doc={self.document_id} p={self.page_number}>"  # pragma: no cover


# =====================================================================
#  Evidence Item Model
# =====================================================================


class EvidenceItemModel(Base):
    """ORM Model representing an indexed text, table, or visual evidence chunk.

    Stores the text/markdown content of a chunk, OCR confidence levels, and coordinate bounding boxes.
    Utilizes PostgreSQL GIN indexes on the `tsv_search` (tsvector) column for Full Text Search.
    """
    __tablename__ = "evidence_items"

    evidence_id = mapped_column(String(50), primary_key=True, default=_new_id)
    case_id = mapped_column(String(50), ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = mapped_column(String(50), ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False, index=True)
    page_id = mapped_column(String(50), ForeignKey("pages.page_id", ondelete="SET NULL"), nullable=True)
    page_number = mapped_column(Integer, nullable=False)

    # Chunk type categorization: 'text_block', 'table_cell', 'table_row', 'page', 'image_region'
    source_type = mapped_column(String(30), nullable=False, default="text_block")
    text = mapped_column(Text, nullable=True)
    markdown = mapped_column(Text, nullable=True)
    bbox = mapped_column(JSONB, nullable=True)
    metadata_json = mapped_column(JSONB, nullable=False, default=dict)
    confidence = mapped_column(Float, nullable=True)

    tsv_search = mapped_column(TSVECTOR, nullable=True)  # FTS index column

    created_at = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # Relationships
    document = relationship("DocumentModel", back_populates="evidence_items_rel")
    page = relationship("PageModel", back_populates="evidence_items_rel")
    embedding = relationship("EvidenceEmbeddingModel", back_populates="evidence_item", uselist=False, cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Evidence {self.evidence_id} doc={self.document_id} p={self.page_number}>"  # pragma: no cover


# Indexing configurations for Full Text Search and fast filtering on Case source types.
__idx_evidence_tsv = Index("idx_evidence_tsv", EvidenceItemModel.tsv_search, postgresql_using="gin")
__idx_evidence_case_type = Index("idx_evidence_case_type", EvidenceItemModel.case_id, EvidenceItemModel.source_type)


# =====================================================================
#  Evidence Embedding Model (pgvector)
# =====================================================================


class EvidenceEmbeddingModel(Base):
    """ORM Model representing vector coordinates for dense retrieval search.

    Links 1-to-1 with an EvidenceItemModel chunk. Uses pgvector to index and query embeddings.
    * `embedding`: Holds 1536-dimensional vectors matching OpenAI `text-embedding-3-small`.
    * `embedding_api`: Holds 3072-dimensional vectors matching Google Gemini API embeddings.
    """
    __tablename__ = "evidence_embeddings"

    embedding_id = mapped_column(String(50), primary_key=True, default=_new_id)
    evidence_id = mapped_column(String(50), ForeignKey("evidence_items.evidence_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    
    embedding = mapped_column(Vector(1536), nullable=False)
    embedding_api = mapped_column(Vector(3072), nullable=True)

    # Relationships
    evidence_item = relationship("EvidenceItemModel", back_populates="embedding")

    def __repr__(self) -> str:
        return f"<Embedding evidence={self.evidence_id}>"  # pragma: no cover


# HNSW indexes for fast cosine distance search using pgvector
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


# =====================================================================
#  Extraction Job Model
# =====================================================================


class ExtractionJobModel(Base):
    """ORM Model representing a schema-constrained batch extraction run.

    Stores job status, start/completion times, and coordinates relationship linkages.
    """
    __tablename__ = "extraction_jobs"

    job_id = mapped_column(String(50), primary_key=True, default=_new_id)
    case_id = mapped_column(String(50), ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False, index=True)
    schema_id = mapped_column(String(50), nullable=False)
    schema_json = mapped_column(JSONB, nullable=True)
    settings = mapped_column(JSONB, nullable=True)
    
    # Status states: 'pending', 'running', 'completed', 'needs_review', 'failed'
    status = mapped_column(String(30), nullable=False, default="pending")
    started_at = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    case = relationship("CaseModel", back_populates="extraction_jobs")
    field_results = relationship("FieldResultModel", back_populates="job", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<ExtractionJob {self.job_id} case={self.case_id} status={self.status}>"  # pragma: no cover


# =====================================================================
#  Field Result Model
# =====================================================================


class FieldResultModel(Base):
    """ORM Model representing the resolved extraction result for a schema property.

    Holds the extracted value, path (e.g. `client_name`), overall property confidence,
    and lists validation errors.
    """
    __tablename__ = "field_results"

    field_result_id = mapped_column(String(50), primary_key=True, default=_new_id)
    job_id = mapped_column(String(50), ForeignKey("extraction_jobs.job_id", ondelete="CASCADE"), nullable=False, index=True)
    field_path = mapped_column(String(200), nullable=False)
    value = mapped_column(JSONB, nullable=True)
    
    # Status: 'validated', 'missing', 'conflict', 'low_confidence', 'invalid', 'human_corrected'
    status = mapped_column(String(30), nullable=False, default="missing")
    confidence = mapped_column(Float, nullable=False, default=0.0)
    validation_errors = mapped_column(JSONB, nullable=False, default=list)
    attempt_count = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    job = relationship("ExtractionJobModel", back_populates="field_results")
    candidates = relationship("FieldCandidateModel", back_populates="field_result", cascade="all, delete-orphan")
    attempts = relationship("FieldAttemptModel", back_populates="field_result", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<FieldResult {self.field_path} status={self.status}>"  # pragma: no cover


# =====================================================================
#  Field Candidate Model
# =====================================================================


class FieldCandidateModel(Base):
    """ORM Model representing an intermediate value prediction generated for a field.

    Points back to the specific chunk evidence ids that supported the prediction hypothesis.
    """
    __tablename__ = "field_candidates"

    candidate_id = mapped_column(String(50), primary_key=True, default=_new_id)
    field_result_id = mapped_column(String(50), ForeignKey("field_results.field_result_id", ondelete="CASCADE"), nullable=False, index=True)
    value = mapped_column(JSONB, nullable=True)
    confidence = mapped_column(Float, nullable=False, default=0.0)
    evidence_ids = mapped_column(ARRAY(String), nullable=False, default=list)
    extraction_method = mapped_column(String(30), nullable=False, default="keyword_rule")

    # Relationships
    field_result = relationship("FieldResultModel", back_populates="candidates")

    def __repr__(self) -> str:
        return f"<Candidate {self.candidate_id} conf={self.confidence}>"  # pragma: no cover


# =====================================================================
#  Field Attempt Model
# =====================================================================


class FieldAttemptModel(Base):
    """ORM Model representing a single LLM execution attempt for a field.

    Tracks token expenditures, model sizes, cost estimates, and exception messages
    for RAG retry loops and audits.
    """
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

    # Relationships
    field_result = relationship("FieldResultModel", back_populates="attempts")

    def __repr__(self) -> str:
        return f"<Attempt {self.attempt_id} field={self.field_result_id} n={self.attempt_number}>"  # pragma: no cover


# =====================================================================
#  Document Job Model (Queue Table)
# =====================================================================


DOCJOB_TASK_TYPES = frozenset({"quick_parse", "deep_parse", "index", "extract_ready_fields"})
DOCJOB_STATUSES = frozenset({"pending", "running", "completed", "failed"})


class DocumentJobModel(Base):
    """ORM Model representing a background queue task for document ingestion.

    Allows worker threads to claim parsing/indexing steps asynchronously.
    """
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

    # Relationships
    document = relationship("DocumentModel", back_populates="document_jobs")

    def __repr__(self) -> str:
        return f"<DocJob {self.job_id} doc={self.document_id} task={self.task_type}>"  # pragma: no cover


# Compound index for fast priority-based queue polling
__idx_docjob_status = Index("idx_docjob_status", DocumentJobModel.status, DocumentJobModel.priority)


# =====================================================================
#  Extraction Result cache
# =====================================================================


class ExtractionResultModel(Base):
    """ORM Model representing cached extraction outputs.

    Provides a quick lookup schema cache to retrieve previously completed runs.
    """
    __tablename__ = "extraction_results"

    run_id = mapped_column(String(50), primary_key=True)
    input_id = mapped_column(String(200), nullable=False, index=True)
    schema_name = mapped_column(String(200), nullable=False)
    response_json = mapped_column(JSONB, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<ExtractionResult {self.run_id} input={self.input_id}>"
