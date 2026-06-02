"""Async SQLAlchemy engine + session factory.

The engine and sessionmaker are created lazily (cached) on first use rather than at import
time, so importing the package never requires a configured database or a DB driver to be
installed. The URL comes from settings (``ACTIONSPLANE_DATABASE_URL``) and uses asyncpg.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from actionsplane.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, created on first use."""
    return create_async_engine(get_settings().database_url, future=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the cached session factory."""
    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields a session and ensures it is closed."""
    async with get_sessionmaker()() as session:
        yield session
