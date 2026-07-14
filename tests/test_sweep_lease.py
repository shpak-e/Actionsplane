"""The reconcile sweep lease is heartbeated, not sized to the worst-case sweep (review 4, NEW-8).

A short TTL means a crashed holder is recovered fast; a background heartbeat re-claims often
enough that a live holder never lapses mid-sweep even though a sweep can outrun the TTL. These
tests drive the ``_sweep_lease`` context manager over a shared in-memory sqlite (StaticPool so the
several sessions it opens see one DB).
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from actionsplane.db.base import Base
from actionsplane.db.models import Lease
from actionsplane.db.repository import claim_lease
from actionsplane.sync import worker


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
async def maker(monkeypatch):
    # StaticPool → every session shares the one in-memory connection, so the lease writes
    # _sweep_lease/_claim_sweep_lease make across their own sessions are all visible.
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(worker, "get_sessionmaker", lambda: m)
    yield m
    await engine.dispose()


async def test_holds_and_heartbeats_the_lease(maker):
    async with worker._sweep_lease("reconcile", ttl_seconds=1, heartbeat_seconds=0.05) as held:
        assert held is True
        async with maker() as s:
            first = (await s.get(Lease, "sweep:reconcile")).expires_at
        await asyncio.sleep(0.16)  # let ≥2 heartbeats fire
        async with maker() as s:
            row = await s.get(Lease, "sweep:reconcile")
            await s.refresh(row)
            later = row.expires_at
        assert later > first  # the heartbeat re-claimed, pushing the expiry forward
        assert row.holder == worker._HOLDER


async def test_skips_when_another_worker_holds_it(maker):
    async with maker() as s:
        await claim_lease(s, name="sweep:reconcile", holder="other:99", ttl_seconds=60)
    async with worker._sweep_lease("reconcile", ttl_seconds=1, heartbeat_seconds=0.05) as held:
        assert held is False
    async with maker() as s:  # the other holder's lease is untouched
        assert (await s.get(Lease, "sweep:reconcile")).holder == "other:99"


async def test_heartbeat_task_is_cancelled_on_exit(maker):
    async with worker._sweep_lease("reconcile", ttl_seconds=1, heartbeat_seconds=0.05):
        pass
    # Give the event loop a tick; no heartbeat task should still be pending/refreshing.
    await asyncio.sleep(0.12)
    async with maker() as s:
        stable = (await s.get(Lease, "sweep:reconcile")).expires_at
    await asyncio.sleep(0.12)
    async with maker() as s:
        row = await s.get(Lease, "sweep:reconcile")
        await s.refresh(row)
        assert row.expires_at == stable  # not moving → the heartbeat really stopped
