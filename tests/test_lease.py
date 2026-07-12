"""Sweep lease claim semantics (Phase 5.3).

The lease is one conditional upsert (claim iff free / expired / already mine), so the whole
two-workers-race surface reduces to these DB-backed cases — exercised on sqlite because the
statement is dialect-portable, exactly like the run-ordering guard tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import Lease
from actionsplane.db.repository import claim_lease


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_second_holder_is_denied_while_lease_live(session):
    assert await claim_lease(session, name="sweep:audit", holder="a:1", ttl_seconds=60) is True
    assert await claim_lease(session, name="sweep:audit", holder="b:2", ttl_seconds=60) is False

    row = await session.get(Lease, "sweep:audit")
    assert row.holder == "a:1"  # the loser didn't overwrite anything


async def test_holder_can_refresh_its_own_lease(session):
    assert await claim_lease(session, name="sweep:audit", holder="a:1", ttl_seconds=60) is True
    # re-claiming while still live is the heartbeat path — extends the TTL, still True
    assert await claim_lease(session, name="sweep:audit", holder="a:1", ttl_seconds=60) is True


async def test_expired_lease_is_taken_over(session):
    # ttl 0 → the lease expires the instant it is claimed; the next claimant takes over
    assert await claim_lease(session, name="sweep:audit", holder="a:1", ttl_seconds=0) is True
    assert await claim_lease(session, name="sweep:audit", holder="b:2", ttl_seconds=60) is True

    row = await session.get(Lease, "sweep:audit")
    await session.refresh(row)
    assert row.holder == "b:2"


async def test_leases_are_independent_per_name(session):
    assert await claim_lease(session, name="sweep:audit", holder="a:1", ttl_seconds=60) is True
    assert await claim_lease(session, name="sweep:drift", holder="b:2", ttl_seconds=60) is True
