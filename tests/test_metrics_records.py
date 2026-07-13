"""metrics_records projects only the columns the metrics functions need (review 3, P1.1).

The per-workflow metrics endpoint used to hydrate full ORM run rows — dragging the heavy
``raw_payload`` JSONB across the wire for up to 2000 runs — then derive durations in Python.
This query selects five columns, derives the two durations in SQL-land, and feeds the pure
``summarize_runs``. These DB-backed cases pin the projection and the derived values.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.repository import metrics_records, upsert_repo, upsert_run
from actionsplane.ingestor import events


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


def _run(run_id, workflow_id, created, started, completed, conclusion="success"):
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "run_number": run_id,
        "head_branch": "main",
        "head_sha": f"sha{run_id}",
        "event": "push",
        "status": "completed",
        "conclusion": conclusion,
        "created_at": created,
        "run_started_at": started,
        "updated_at": completed,
        "actor": {"login": "octocat"},
        "run_attempt": 1,
        # a bulky payload that must NOT come back through the projection
        "big": "x" * 10_000,
    }


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await upsert_repo(
            s,
            {"id": 1, "owner": "demo", "name": "api", "default_branch": "main", "archived": False},
            installation_id=1,
        )
        yield s
    await engine.dispose()


async def _seed(session, run):
    # normalize_run_object also needs completed_at; the normalizer derives it from the payload.
    row = events.normalize_run_object(run, repo_id=1)
    await upsert_run(session, row)
    await session.commit()


async def test_records_derive_duration_and_queue(session):
    await _seed(
        session,
        _run(1, 7, "2026-06-01T10:00:00Z", "2026-06-01T10:00:05Z", "2026-06-01T10:02:05Z"),
    )
    records = await metrics_records(session, workflow_id=7, limit=100)
    assert len(records) == 1
    r = records[0]
    assert set(r) == {"conclusion", "head_sha", "duration_s", "queue_s"}  # nothing bulky
    assert r["conclusion"] == "success"
    assert r["head_sha"] == "sha1"
    assert r["duration_s"] == 120.0  # 10:02:05 minus 10:00:05
    assert r["queue_s"] == 5.0  # 10:00:05 minus 10:00:00


async def test_only_the_requested_workflow_newest_first_and_limited(session):
    await _seed(
        session,
        _run(1, 7, "2026-06-01T10:00:00Z", "2026-06-01T10:00:00Z", "2026-06-01T10:01:00Z"),
    )
    await _seed(
        session,
        _run(2, 7, "2026-06-02T10:00:00Z", "2026-06-02T10:00:00Z", "2026-06-02T10:01:00Z"),
    )
    await _seed(
        session,
        _run(3, 8, "2026-06-03T10:00:00Z", "2026-06-03T10:00:00Z", "2026-06-03T10:01:00Z"),
    )
    records = await metrics_records(session, workflow_id=7, limit=1)
    assert len(records) == 1  # limit honoured
    assert records[0]["head_sha"] == "sha2"  # newest by created_at, and workflow 8 excluded
