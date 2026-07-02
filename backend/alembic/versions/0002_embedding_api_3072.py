"""resize Gemini API embeddings to 3072 dimensions

Revision ID: 0002_embedding_api_3072
Revises: 0001_init_production_tables
Create Date: 2026-07-01
"""
from __future__ import annotations

from alembic import op


revision = "0002_embedding_api_3072"
down_revision = "0001_init_production_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_embedding_api_hnsw")
    op.execute("ALTER TABLE evidence_embeddings DROP COLUMN IF EXISTS embedding_api")
    op.execute("ALTER TABLE evidence_embeddings ADD COLUMN embedding_api vector(3072)")
    op.execute("CREATE INDEX idx_embedding_api_hnsw ON evidence_embeddings USING hnsw (embedding_api vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_embedding_api_hnsw")
    op.execute("ALTER TABLE evidence_embeddings DROP COLUMN IF EXISTS embedding_api")
    op.execute("ALTER TABLE evidence_embeddings ADD COLUMN embedding_api vector(1536)")
    op.execute("CREATE INDEX idx_embedding_api_hnsw ON evidence_embeddings USING hnsw (embedding_api vector_cosine_ops)")
