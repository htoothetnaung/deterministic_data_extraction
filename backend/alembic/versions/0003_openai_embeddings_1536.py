"""normalize evidence embeddings to OpenAI 1536 dimensions

Revision ID: 0003_openai_embeddings_1536
Revises: 0002_embedding_api_3072
Create Date: 2026-07-02
"""
from __future__ import annotations

from alembic import op


revision = "0003_openai_embeddings_1536"
down_revision = "0002_embedding_api_3072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing 768/3072 vectors cannot be converted into 1536-d OpenAI vectors.
    # Evidence rows stay intact; rerun indexing to regenerate dense vectors.
    op.execute("DROP INDEX IF EXISTS idx_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_embedding_api_hnsw")
    op.execute("TRUNCATE TABLE evidence_embeddings")
    op.execute("ALTER TABLE evidence_embeddings ALTER COLUMN embedding TYPE vector(1536)")
    op.execute("ALTER TABLE evidence_embeddings ALTER COLUMN embedding_api TYPE vector(1536)")
    op.execute("CREATE INDEX idx_embedding_hnsw ON evidence_embeddings USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX idx_embedding_api_hnsw ON evidence_embeddings USING hnsw (embedding_api vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_embedding_api_hnsw")
    op.execute("TRUNCATE TABLE evidence_embeddings")
    op.execute("ALTER TABLE evidence_embeddings ALTER COLUMN embedding TYPE vector(768)")
    op.execute("ALTER TABLE evidence_embeddings ALTER COLUMN embedding_api TYPE vector(3072)")
    op.execute("CREATE INDEX idx_embedding_hnsw ON evidence_embeddings USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX idx_embedding_api_hnsw ON evidence_embeddings USING hnsw (embedding_api vector_cosine_ops)")
