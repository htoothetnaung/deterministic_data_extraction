"""init production extraction tables

Revision ID: 0001_init_production_tables
Revises:
Create Date: 2026-06-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision = "0001_init_production_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "cases",
        sa.Column("case_id", sa.String(length=50), primary_key=True),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "documents",
        sa.Column("document_id", sa.String(length=50), primary_key=True),
        sa.Column("case_id", sa.String(length=50), sa.ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_hash", sa.String(length=64)),
        sa.Column("storage_path", sa.String(length=1000)),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("user_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("inferred_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parser_status", sa.String(length=30), nullable=False),
        sa.Column("parse_quality", sa.String(length=20)),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("failure_info", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_case_id", "documents", ["case_id"])
    op.create_index("ix_documents_file_hash", "documents", ["file_hash"])
    op.create_index("idx_documents_parser_status", "documents", ["parser_status"])

    op.create_table(
        "pages",
        sa.Column("page_id", sa.String(length=50), primary_key=True),
        sa.Column("document_id", sa.String(length=50), sa.ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("markdown", sa.Text()),
        sa.Column("image_path", sa.String(length=1000)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("parse_quality", sa.String(length=20)),
    )
    op.create_index("ix_pages_document_id", "pages", ["document_id"])

    op.create_table(
        "evidence_items",
        sa.Column("evidence_id", sa.String(length=50), primary_key=True),
        sa.Column("case_id", sa.String(length=50), sa.ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(length=50), sa.ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_id", sa.String(length=50), sa.ForeignKey("pages.page_id", ondelete="SET NULL")),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("markdown", sa.Text()),
        sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("tsv_search", postgresql.TSVECTOR()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_items_case_id", "evidence_items", ["case_id"])
    op.create_index("ix_evidence_items_document_id", "evidence_items", ["document_id"])
    op.create_index("idx_evidence_case_type", "evidence_items", ["case_id", "source_type"])
    op.create_index("idx_evidence_tsv", "evidence_items", ["tsv_search"], postgresql_using="gin")

    op.create_table(
        "evidence_embeddings",
        sa.Column("embedding_id", sa.String(length=50), primary_key=True),
        sa.Column("evidence_id", sa.String(length=50), sa.ForeignKey("evidence_items.evidence_id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("embedding_api", Vector(3072), nullable=True),
    )
    op.create_index("ix_evidence_embeddings_evidence_id", "evidence_embeddings", ["evidence_id"])
    op.execute("CREATE INDEX idx_embedding_hnsw ON evidence_embeddings USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX idx_embedding_api_hnsw ON evidence_embeddings USING hnsw (embedding_api vector_cosine_ops)")

    op.create_table(
        "extraction_jobs",
        sa.Column("job_id", sa.String(length=50), primary_key=True),
        sa.Column("case_id", sa.String(length=50), sa.ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_id", sa.String(length=50), nullable=False),
        sa.Column("schema_json", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_extraction_jobs_case_id", "extraction_jobs", ["case_id"])

    op.create_table(
        "field_results",
        sa.Column("field_result_id", sa.String(length=50), primary_key=True),
        sa.Column("job_id", sa.String(length=50), sa.ForeignKey("extraction_jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_path", sa.String(length=200), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("validation_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_field_results_job_id", "field_results", ["job_id"])

    op.create_table(
        "field_candidates",
        sa.Column("candidate_id", sa.String(length=50), primary_key=True),
        sa.Column("field_result_id", sa.String(length=50), sa.ForeignKey("field_results.field_result_id", ondelete="CASCADE"), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("extraction_method", sa.String(length=30), nullable=False),
    )
    op.create_index("ix_field_candidates_field_result_id", "field_candidates", ["field_result_id"])

    op.create_table(
        "field_attempts",
        sa.Column("attempt_id", sa.String(length=50), primary_key=True),
        sa.Column("field_result_id", sa.String(length=50), sa.ForeignKey("field_results.field_result_id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("evidence_pack", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("model_used", sa.String(length=100)),
        sa.Column("cost", sa.Numeric(12, 6)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_field_attempts_field_result_id", "field_attempts", ["field_result_id"])

    op.create_table(
        "document_jobs",
        sa.Column("job_id", sa.String(length=50), primary_key=True),
        sa.Column("document_id", sa.String(length=50), sa.ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_document_jobs_document_id", "document_jobs", ["document_id"])
    op.create_index("idx_docjob_status", "document_jobs", ["status", "priority"])


def downgrade() -> None:
    op.drop_index("idx_docjob_status", table_name="document_jobs")
    op.drop_index("ix_document_jobs_document_id", table_name="document_jobs")
    op.drop_table("document_jobs")
    op.drop_index("ix_field_attempts_field_result_id", table_name="field_attempts")
    op.drop_table("field_attempts")
    op.drop_index("ix_field_candidates_field_result_id", table_name="field_candidates")
    op.drop_table("field_candidates")
    op.drop_index("ix_field_results_job_id", table_name="field_results")
    op.drop_table("field_results")
    op.drop_index("ix_extraction_jobs_case_id", table_name="extraction_jobs")
    op.drop_table("extraction_jobs")
    op.execute("DROP INDEX IF EXISTS idx_embedding_api_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_embedding_ivfflat")
    op.drop_index("ix_evidence_embeddings_evidence_id", table_name="evidence_embeddings")
    op.drop_table("evidence_embeddings")
    op.drop_index("idx_evidence_tsv", table_name="evidence_items")
    op.drop_index("idx_evidence_case_type", table_name="evidence_items")
    op.drop_index("ix_evidence_items_document_id", table_name="evidence_items")
    op.drop_index("ix_evidence_items_case_id", table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_index("ix_pages_document_id", table_name="pages")
    op.drop_table("pages")
    op.drop_index("idx_documents_parser_status", table_name="documents")
    op.drop_index("ix_documents_file_hash", table_name="documents")
    op.drop_index("ix_documents_case_id", table_name="documents")
    op.drop_table("documents")
    op.drop_table("cases")
