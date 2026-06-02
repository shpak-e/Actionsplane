"""Alembic environment — async engine, autogenerate against the ORM metadata.

The database URL comes from settings (``ACTIONSPLANE_DATABASE_URL``) rather than alembic.ini,
so migrations target the same DB as the app in every environment. Importing db.models
registers all tables on ``Base.metadata`` for autogenerate.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from actionsplane.config import get_settings
from actionsplane.db import models  # registers tables on Base.metadata
from actionsplane.db.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
