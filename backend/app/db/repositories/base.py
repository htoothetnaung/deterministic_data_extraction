"""Base repository with common async CRUD helpers."""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import Base

M = TypeVar("M", bound=Base)


class BaseRepository(Generic[M]):
    """Thin async CRUD wrapper around a SQLAlchemy model."""

    def __init__(self, session: AsyncSession, model: type[M]) -> None:
        self.session = session
        self.model = model

    async def get(self, pk: str) -> M | None:
        stmt = select(self.model).where(self.model.__table__.primary_key.columns.values()[0] == pk)  # type: ignore[union-attr]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, **filters: Any) -> list[M]:
        stmt = select(self.model)
        for column, value in filters.items():
            if value is not None:
                col = getattr(self.model, column, None)
                if col is not None:
                    stmt = stmt.where(col == value)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, instance: M) -> M:
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def delete(self, pk: str) -> bool:
        instance = await self.get(pk)
        if instance is None:
            return False
        await self.session.delete(instance)
        await self.session.flush()
        return True

    async def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model)
        for column, value in filters.items():
            if value is not None:
                col = getattr(self.model, column, None)
                if col is not None:
                    stmt = stmt.where(col == value)
        result = await self.session.execute(stmt)
        return result.scalar_one()
