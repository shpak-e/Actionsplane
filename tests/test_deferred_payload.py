"""raw_payload is deferred off hot queries but undeferred where the step list is read (§5 H1).

The JSONB payload is written on every event and never read back except for the job step list.
Deferring it keeps MBs of dead weight out of every run/job SELECT; the two job queries that feed
the failing-step / step-tree UI opt back in. These assertions inspect the loaded/unloaded state.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.repository import list_jobs, list_runs, upsert_job, upsert_repo, upsert_run
from actionsplane.ingestor import events


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
        await upsert_repo(
            s,
            {"id": 1, "owner": "demo", "name": "api", "default_branch": "main", "archived": False},
            installation_id=1,
        )
        run = {
            "id": 5,
            "repo_id": 1,
            "workflow_id": 9,
            "run_number": 1,
            "head_branch": "main",
            "head_sha": "sha",
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-06-01T10:00:00Z",
            "run_started_at": "2026-06-01T10:00:00Z",
            "updated_at": "2026-06-01T10:01:00Z",
            "actor": {"login": "octocat"},
            "run_attempt": 1,
        }
        await upsert_run(s, events.normalize_run_object(run, repo_id=1))
        await upsert_job(
            s,
            events.normalize_workflow_job(
                {
                    "workflow_job": {
                        "id": 50,
                        "run_id": 5,
                        "name": "build",
                        "status": "completed",
                        "conclusion": "failure",
                        "started_at": "2026-06-01T10:00:00Z",
                        "completed_at": "2026-06-01T10:01:00Z",
                        "steps": [{"name": "test", "status": "completed", "conclusion": "failure"}],
                    }
                }
            ),
        )
        await s.commit()
        yield s
    await engine.dispose()


async def test_run_list_does_not_load_raw_payload(session):
    (run,) = await list_runs(session, repo_id=1)
    assert "raw_payload" in inspect(run).unloaded  # deferred — not fetched


async def test_list_jobs_undefers_raw_payload(session):
    (job,) = await list_jobs(session, run_id=5)
    assert "raw_payload" not in inspect(job).unloaded  # explicitly undeferred
    assert job.raw_payload["steps"][0]["name"] == "test"  # and usable without a lazy load
