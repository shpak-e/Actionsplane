"""Payload retention/pruning (Phase 5.6).

``raw_payload`` on old runs/jobs is nulled (normalized columns survive, so history stays
queryable) and old processed-delivery ids are deleted — both in LIMIT-batched loops. The worker
cron is lease-guarded like the sweeps. sqlite-backed; StaticPool so the worker task's separate
sessions all see the same in-memory DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from actionsplane.db.base import Base
from actionsplane.db.models import ProcessedDelivery, WorkflowJob, WorkflowRun
from actionsplane.db.repository import (
    claim_lease,
    prune_deliveries,
    prune_job_payloads,
    prune_run_payloads,
)
from actionsplane.sync.worker import prune_retention

NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=100)
RECENT = NOW - timedelta(days=1)
CUTOFF = NOW - timedelta(days=90)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


def _run(run_id: int, created_at: datetime) -> WorkflowRun:
    return WorkflowRun(
        id=run_id,
        repo_id=1,
        run_number=run_id,
        status="completed",
        conclusion="success",
        created_at=created_at,
        raw_payload={"id": run_id, "bulky": "x" * 10},
    )


def _job(job_id: int, *, completed_at: datetime | None, started_at: datetime | None) -> WorkflowJob:
    return WorkflowJob(
        id=job_id,
        run_id=1,
        name="build",
        status="completed",
        completed_at=completed_at,
        started_at=started_at,
        raw_payload={"id": job_id},
    )


@pytest.fixture
async def db():
    # StaticPool: every session shares the single in-memory connection, so the worker task's
    # own sessions (lease claim + prune) operate on the same database as the seeding session.
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add_all([_run(1, OLD), _run(2, RECENT), _run(3, OLD)])
        s.add_all(
            [
                _job(11, completed_at=OLD, started_at=OLD),
                _job(12, completed_at=RECENT, started_at=RECENT),
                _job(13, completed_at=None, started_at=OLD),  # aged by started_at fallback
                _job(14, completed_at=None, started_at=None),  # unageable — never pruned
            ]
        )
        s.add_all(
            [
                ProcessedDelivery(delivery_id="old-1", event_type="workflow_run", seen_at=OLD),
                ProcessedDelivery(delivery_id="new-1", event_type="workflow_run", seen_at=RECENT),
            ]
        )
        await s.commit()
    yield maker
    await engine.dispose()


async def test_prune_run_payloads_batched(db):
    async with db() as session:
        # batch_size=1 forces the select→update→commit loop to iterate
        pruned = await prune_run_payloads(session, cutoff=CUTOFF, batch_size=1)
        assert pruned == 2

        rows = {r.id: r for r in (await session.scalars(select(WorkflowRun))).all()}
        for row in rows.values():
            await session.refresh(row)
        assert rows[1].raw_payload is None
        assert rows[3].raw_payload is None
        assert rows[2].raw_payload is not None  # recent row untouched
        # normalized columns survive — history stays queryable
        assert rows[1].status == "completed"
        assert rows[1].conclusion == "success"
        assert rows[1].created_at is not None

        # idempotent: a second pass finds nothing left to prune
        assert await prune_run_payloads(session, cutoff=CUTOFF, batch_size=1) == 0


async def test_prune_job_payloads_uses_completed_then_started(db):
    async with db() as session:
        pruned = await prune_job_payloads(session, cutoff=CUTOFF, batch_size=1)
        assert pruned == 2

        rows = {j.id: j for j in (await session.scalars(select(WorkflowJob))).all()}
        for row in rows.values():
            await session.refresh(row)
        assert rows[11].raw_payload is None  # old completed_at
        assert rows[13].raw_payload is None  # no completed_at, old started_at
        assert rows[12].raw_payload is not None  # recent
        assert rows[14].raw_payload is not None  # no timestamps → can't age → kept
        assert rows[11].name == "build"  # normalized columns survive


async def test_prune_deliveries_deletes_old_rows(db):
    async with db() as session:
        pruned = await prune_deliveries(session, cutoff=CUTOFF, batch_size=1)
        assert pruned == 1

        left = (await session.scalars(select(ProcessedDelivery.delivery_id))).all()
        assert list(left) == ["new-1"]


@pytest.fixture
def worker_env(db, monkeypatch):
    """Point the worker cron at the test DB + fixed retention settings."""
    monkeypatch.setattr("actionsplane.sync.worker.get_sessionmaker", lambda: db)
    monkeypatch.setattr(
        "actionsplane.sync.worker.get_settings",
        lambda: SimpleNamespace(raw_payload_retention_days=90, delivery_retention_days=30),
    )
    return db


async def test_prune_retention_cron_end_to_end(worker_env):
    pruned = await prune_retention({})
    assert pruned == 5  # 2 runs + 2 jobs + 1 delivery

    async with worker_env() as session:
        assert (await session.get(WorkflowRun, 1)).raw_payload is None
        assert (await session.get(WorkflowRun, 2)).raw_payload is not None


async def test_prune_retention_skips_when_lease_held_elsewhere(worker_env):
    async with worker_env() as session:
        assert await claim_lease(
            session, name="sweep:prune", holder="other-worker:1", ttl_seconds=300
        )

    assert await prune_retention({}) == 0  # lease denied → skipped, nothing pruned

    async with worker_env() as session:
        assert (await session.get(WorkflowRun, 1)).raw_payload is not None
