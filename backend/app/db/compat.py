"""Small database compatibility migrations for local development schemas."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession


async def ensure_runtime_settings_columns(target: AsyncConnection | AsyncSession) -> None:
    """Add JSONB settings columns introduced after older local DBs were created."""
    await target.execute(text("ALTER TABLE cases ADD COLUMN IF NOT EXISTS settings JSONB"))
    await target.execute(text("ALTER TABLE extraction_jobs ADD COLUMN IF NOT EXISTS settings JSONB"))
