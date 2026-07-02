"""Async database engine, session factory, and helper utilities."""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


_engine = None
_factory = None


def _get_async_driver(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def create_engine() -> None:
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
    return _engine


def get_factory():
    return _factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
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
    if _engine is None:
        return
    async with _engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _factory = None


def is_db_configured() -> bool:
    return bool(settings.database_url) and _engine is not None
