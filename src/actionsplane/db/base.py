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
    """Return the process-wide async engine, created on first use.

    Bounded pool + ``pool_pre_ping`` so a busy API/worker can't exhaust Postgres connections or
    hand out one the server already dropped after an idle gap. On Postgres we also set server-side
    ``statement_timeout`` / ``idle_in_transaction_session_timeout`` ceilings so a pathological query
    or a leaked transaction can't pin a connection indefinitely. sqlite (tests) takes none of this —
    it has no such pool or GUCs — so the extra kwargs are applied only for a real DB URL.
    """
    settings = get_settings()
    url = settings.database_url
    kwargs: dict = {"future": True}
    if not url.startswith("sqlite"):
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
        )
        # asyncpg applies these libpq GUCs per connection; 0 means "no timeout" to Postgres.
        server_settings = {
            "statement_timeout": str(max(0, settings.db_statement_timeout_ms)),
            "idle_in_transaction_session_timeout": str(max(0, settings.db_idle_in_txn_timeout_ms)),
        }
        if url.startswith("postgresql"):
            kwargs["connect_args"] = {"server_settings": server_settings}
    return create_async_engine(url, **kwargs)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the cached session factory."""
    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields a session and ensures it is closed."""
    async with get_sessionmaker()() as session:
        yield session
