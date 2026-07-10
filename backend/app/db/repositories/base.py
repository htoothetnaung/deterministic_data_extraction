"""Base repository with common async CRUD helpers."""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import Base

M = TypeVar("M", bound=Base)


class BaseRepository(Generic[M]):
    """Thin async CRUD wrapper around a SQLAlchemy model.

    Provides common CRUD helpers that all subclasses inherit, abstracting away
    boilerplate query construction and session operations.
    """

    def __init__(self, session: AsyncSession, model: type[M]) -> None:
        """Initialize the repository with an active SQLAlchemy AsyncSession and model type.

        The AsyncSession handles transactions, connection pooling, and flushing.
        """
        self.session = session
        self.model = model

    async def get(self, pk: str) -> M | None:
        """Retrieve a single database model instance by its primary key.

        Uses the dynamically resolved primary key column of the model class.
        Returns None if no matching record is found.
        """
        stmt = select(self.model).where(self.model.__table__.primary_key.columns.values()[0] == pk)  # type: ignore[union-attr]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, **filters: Any) -> list[M]:
        """Query multiple model instances with optional column-value filters.

        Accepts keyword arguments matching model column names, ignoring any None values.
        Constructs and executes a SELECT statement return a list of model instances.
        """
        stmt = select(self.model)
        for column, value in filters.items():
            if value is not None:
                col = getattr(self.model, column, None)
                if col is not None:
                    stmt = stmt.where(col == value)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, instance: M) -> M:
        """Add a new model instance to the database session and flush.

        Adds the model object to SQLAlchemy's identity map. `session.flush()` is called
        to synchronize changes with the database and populate generated columns (like primary keys
        or timestamps) without committing the active transaction yet.
        """
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def delete(self, pk: str) -> bool:
        """Delete a record by its primary key.

        First attempts to fetch the object. If found, deletes it from the database session
        and flushes the changes. Returns True if the deletion succeeded, and False if the
        record did not exist.
        """
        instance = await self.get(pk)
        if instance is None:
            return False
        await self.session.delete(instance)
        await self.session.flush()
        return True

    async def count(self, **filters: Any) -> int:
        """Count the total number of records matching the optional column-value filters.

        Constructs a `select(func.count())` query. Ignores None filter values. Useful for
        pagination metadata or queue metrics (e.g. pending document counts).
        """
        stmt = select(func.count()).select_from(self.model)
        for column, value in filters.items():
            if value is not None:
                col = getattr(self.model, column, None)
                if col is not None:
                    stmt = stmt.where(col == value)
        result = await self.session.execute(stmt)
        return result.scalar_one()
