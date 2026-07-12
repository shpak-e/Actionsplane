"""Out-of-order webhook guard for workflow_jobs (Phase 5.4 — mirrors the run guard from 0008).

``workflow_job`` events are delivered at-least-once and out of order, but — unlike runs — the
payload carries no monotonic ``updated_at``. The upsert therefore gates on a status *rank*
(queued=0 < in_progress=1 < completed=2) computed inline in SQL, so a late ``in_progress``
redelivery can't reopen a stored ``completed`` job. Verified end-to-end against in-memory
sqlite — the conditional upsert is dialect-portable, same as ``test_repository_run_ordering``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import WorkflowJob
from actionsplane.db.repository import upsert_job
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


def _job_event(status: str, conclusion: str | None, *, runner: str | None = None) -> dict:
    """A bare workflow_job webhook payload at a given lifecycle point (fixed job id)."""
    return {
        "workflow_job": {
            "id": 777,
            "run_id": 555,
            "name": "build",
            "status": status,
            "conclusion": conclusion,
            "started_at": "2026-06-01T10:00:10Z",
            "completed_at": "2026-06-01T10:03:00Z" if status == "completed" else None,
            "runner_name": runner,
            "runner_group_name": None,
            "labels": ["ubuntu-latest"],
        }
    }


async def _upsert(session: AsyncSession, payload: dict) -> None:
    await upsert_job(session, events.normalize_workflow_job(payload))
    await session.commit()


async def test_late_in_progress_does_not_regress_completed(session):
    # completed lands first, then a late in_progress redelivery for the same job id
    await _upsert(session, _job_event("completed", "success"))
    await _upsert(session, _job_event("in_progress", None))

    row = await session.get(WorkflowJob, 777)
    await session.refresh(row)
    assert row.status == "completed"
    assert row.conclusion == "success"
    assert row.completed_at is not None  # the stale event didn't blank the completion time


async def test_late_queued_does_not_regress_in_progress(session):
    await _upsert(session, _job_event("in_progress", None))
    await _upsert(session, _job_event("queued", None))

    row = await session.get(WorkflowJob, 777)
    await session.refresh(row)
    assert row.status == "in_progress"


async def test_forward_transitions_still_apply(session):
    await _upsert(session, _job_event("queued", None))
    await _upsert(session, _job_event("in_progress", None, runner="runner-7"))
    await _upsert(session, _job_event("completed", "failure"))

    row = await session.get(WorkflowJob, 777)
    await session.refresh(row)
    assert row.status == "completed"
    assert row.conclusion == "failure"


async def test_equal_rank_completed_can_update_conclusion(session):
    # equal-completed still applies, so a corrected conclusion redelivery lands
    await _upsert(session, _job_event("completed", "failure"))
    await _upsert(session, _job_event("completed", "success"))

    row = await session.get(WorkflowJob, 777)
    await session.refresh(row)
    assert row.conclusion == "success"


async def test_first_write_inserts(session):
    await _upsert(session, _job_event("queued", None))

    row = await session.get(WorkflowJob, 777)
    assert row.status == "queued"
