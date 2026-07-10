"""Async database engine, session factory, and helper utilities.

This module initializes the SQLAlchemy async database engine (backed by asyncpg for Postgres),
creates the session factory, handles connection pool lifecycles, and exposes session generators.
"""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.db.compat import ensure_runtime_settings_columns


class Base(DeclarativeBase):
    """SQLAlchemy Declarative Base class.

    All mapped database models inherit from this base class, allowing them to share the
    metadata registry used to issue DDl and run model mappings.
    """
    pass


# Global singleton references for the database connection engine and session factory.
_engine = None
_factory = None


def _get_async_driver(url: str) -> str:
    """Ensure the database URL prefix is compatible with SQLAlchemy's asyncpg driver.

    If a URL starts with standard synchronous 'postgresql://', replaces it with
    the asynchronous driver dialect 'postgresql+asyncpg://'.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def create_engine() -> None:
    """Initialize the global async engine and session factory.

    Configures connection parameters:
    * `echo`: logs raw SQL calls (active in debug/development mode).
    * `pool_pre_ping`: runs a check query (e.g. SELECT 1) on checkouts to discard stale connections.
    """
    global _engine, _factory
    if _engine is not None:
        return
    db_url = settings.database_url
    if not db_url:
        return
    async_url = _get_async_driver(db_url)
    _engine = create_async_engine(async_url, echo=settings.debug, pool_pre_ping=True)
    _factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


def get_engine():
    """Retrieve the active SQLAlchemy AsyncEngine instance. Returns None if unconfigured."""
    return _engine


def get_factory():
    """Retrieve the active SQLAlchemy sessionmaker factory. Returns None if unconfigured."""
    return _factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency generator yielding a database session for request lifecycles.

    Initializes the engine on first checkout, opens a connection, and safely closes
    it in a `finally` block once the API controller finishes processing.
    """
    if _factory is None and settings.database_url:
        create_engine()
    if _factory is None:
        raise RuntimeError("Database not configured. Set EXTRACT_DATABASE_URL.")
    async with _factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize tables and custom extensions in the target database.

    Runs during application startup:
    1. Ensures pgvector's "vector" extension is loaded in Postgres.
    2. Runs schema creation helper (`create_all`) to construct tables that do not exist yet.
    """
    if _engine is None:
        return
    async with _engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await ensure_runtime_settings_columns(conn)


async def dispose_db() -> None:
    """Gracefully close all active connections in the connection pool.

    Called during application shutdown to clean up open TCP sockets.
    """
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _factory = None


def is_db_configured() -> bool:
    """Check whether a database connection string has been configured and the engine is initialized."""
    return bool(settings.database_url) and _engine is not None
