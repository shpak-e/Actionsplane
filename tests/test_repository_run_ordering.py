"""Out-of-order webhook guard for workflow_runs (staff-review S3 / resume §4.1).

GitHub redelivers `workflow_run` events at-least-once and out of order, so a late
`in_progress` event can arrive after the `completed` event for the same run id. `upsert_run`
gates the update on the run's monotonic `updated_at` so a stale event can't regress a fresher
row. Verified end-to-end against an in-memory sqlite DB — the upsert is dialect-portable, so
this exercises the real ON CONFLICT ... DO UPDATE ... WHERE path without a live Postgres.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import WorkflowRun
from actionsplane.db.repository import upsert_run
from actionsplane.ingestor import events


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    """Render the Postgres-only JSONB columns as JSON so create_all works on sqlite."""
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


def _run(status: str, conclusion: str | None, updated_at: str) -> dict:
    """A bare GitHub run object at a given lifecycle point (run id is fixed across events)."""
    return {
        "id": 555,
        "workflow_id": None,
        "run_number": 7,
        "head_branch": "main",
        "head_sha": "deadbeef",
        "event": "push",
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-06-01T10:00:00Z",
        "run_started_at": "2026-06-01T10:00:05Z",
        "updated_at": updated_at,
        "actor": {"login": "octocat"},
        "run_attempt": 1,
    }


async def _upsert(session: AsyncSession, run: dict) -> int:
    rc = await upsert_run(session, events.normalize_run_object(run, repo_id=1))
    await session.commit()
    return rc


async def test_late_in_progress_does_not_clobber_completed(session):
    # completed lands first (updated_at 10:05), then a late in_progress redelivery (10:02).
    await _upsert(session, _run("completed", "success", "2026-06-01T10:05:00Z"))
    await _upsert(session, _run("in_progress", None, "2026-06-01T10:02:00Z"))

    row = await session.get(WorkflowRun, 555)
    assert row.status == "completed"
    assert row.conclusion == "success"


async def test_newer_event_still_updates(session):
    # Forward path is unaffected: in_progress (10:02) → completed (10:05) advances the row.
    await _upsert(session, _run("in_progress", None, "2026-06-01T10:02:00Z"))
    await _upsert(session, _run("completed", "success", "2026-06-01T10:05:00Z"))

    row = await session.get(WorkflowRun, 555)
    assert row.status == "completed"
    assert row.conclusion == "success"


async def test_first_write_inserts(session):
    # No prior row → the guard never fires, the run is inserted as-is.
    await _upsert(session, _run("queued", None, "2026-06-01T10:01:00Z"))

    row = await session.get(WorkflowRun, 555)
    assert row.status == "queued"


async def test_identical_redelivery_writes_no_row(session):
    # The reconcile hot path: replaying an already-seen run. The strict guard (4a) must write 0
    # rows for a byte-identical redelivery — no churn, no index dirtying on an idle repo.
    assert await _upsert(session, _run("completed", "success", "2026-06-01T10:05:00Z")) == 1
    assert await _upsert(session, _run("completed", "success", "2026-06-01T10:05:00Z")) == 0


async def test_equal_timestamp_conclusion_correction_applies(session):
    # Same updated_at, but the conclusion changes (a correction) → the write must still land,
    # mirroring the job gate's equal-rank nuance.
    assert await _upsert(session, _run("completed", None, "2026-06-01T10:05:00Z")) == 1
    assert await _upsert(session, _run("completed", "success", "2026-06-01T10:05:00Z")) == 1
    row = await session.get(WorkflowRun, 555)
    assert row.conclusion == "success"
