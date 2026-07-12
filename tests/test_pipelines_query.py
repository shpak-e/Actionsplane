"""DB-backed: /pipelines status queries are O(1) and correct at scale (review 3, item 1).

Two things the Pipelines graph relies on, exercised on sqlite (JSONB→JSON shim):

* ``latest_runs_for`` — newest run per workflow via a window function, tie-broken by id, and
  projecting *only* the status columns (never ``raw_payload``). One query for N workflows.
* ``_failing_steps_for`` — the failing (job, step) for many runs in a single query, replacing the
  old per-node fetch. A query-counter pins both against regressing back to N+1.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.api.app import _failing_steps_for
from actionsplane.db.base import Base
from actionsplane.db.models import Workflow
from actionsplane.db.repository import (
    LatestRun,
    latest_runs_for,
    upsert_job,
    upsert_repo,
    upsert_run,
)
from actionsplane.ingestor import events


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


def _run(run_id, workflow_id, run_number, created_at, conclusion="success"):
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "run_number": run_number,
        "head_branch": "main",
        "head_sha": "sha",
        "event": "push",
        "status": "completed",
        "conclusion": conclusion,
        "created_at": created_at,
        "run_started_at": created_at,
        "updated_at": created_at,
        "actor": {"login": "octocat"},
        "run_attempt": 1,
    }


def _job(job_id, run_id, name, conclusion, steps):
    return {
        "workflow_job": {
            "id": job_id,
            "run_id": run_id,
            "name": name,
            "status": "completed",
            "conclusion": conclusion,
            "started_at": "2026-06-01T10:00:00Z",
            "completed_at": "2026-06-01T10:02:00Z",
            "steps": steps,
        }
    }


class _Counter:
    def __init__(self) -> None:
        self.n = 0

    def reset(self) -> None:
        self.n = 0


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    counter = _Counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _count(*_args):
        counter.n += 1

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await upsert_repo(
            s,
            {"id": 1, "owner": "demo", "name": "api", "default_branch": "main", "archived": False},
            installation_id=1,
        )
        for wid, path in [(1, "a.yml"), (2, "b.yml"), (3, "c.yml")]:
            await s.merge(Workflow(id=wid, repo_id=1, path=f".github/workflows/{path}", name=path))
        await s.commit()
        yield s, counter
    await engine.dispose()


async def test_latest_run_per_workflow_one_query_no_payload(db):
    session, counter = db
    # wf1: newer run by created_at wins. wf2: SAME created_at → higher id wins (tie-break).
    # wf3: single run. Several older/other runs present to prove we pick the newest, not all.
    await upsert_run(
        session, events.normalize_run_object(_run(101, 1, 1, "2026-06-01T10:00:00Z"), 1)
    )
    await upsert_run(
        session, events.normalize_run_object(_run(102, 1, 2, "2026-06-01T10:05:00Z"), 1)
    )
    await upsert_run(
        session, events.normalize_run_object(_run(201, 2, 1, "2026-06-01T10:00:00Z"), 1)
    )
    await upsert_run(
        session, events.normalize_run_object(_run(202, 2, 2, "2026-06-01T10:00:00Z"), 1)
    )
    await upsert_run(
        session, events.normalize_run_object(_run(301, 3, 7, "2026-06-01T09:00:00Z", "failure"), 1)
    )
    await session.commit()

    counter.reset()
    latest = await latest_runs_for(session, [1, 2, 3])

    assert counter.n == 1  # single window-function query regardless of run history size
    assert latest[1].id == 102 and latest[1].run_number == 2  # newest by created_at
    assert latest[2].id == 202 and latest[2].run_number == 2  # created_at tie broken by id desc
    assert latest[3].id == 301 and latest[3].conclusion == "failure"
    assert isinstance(latest[1], LatestRun)
    assert not hasattr(latest[1], "raw_payload")  # heavy JSONB never projected


async def test_latest_runs_for_empty_input_no_query(db):
    session, counter = db
    counter.reset()
    assert await latest_runs_for(session, []) == {}
    assert counter.n == 0


async def test_failing_steps_batched_one_query(db):
    session, counter = db
    for rid in (401, 402, 403):
        await upsert_run(
            session,
            events.normalize_run_object(_run(rid, 1, rid, "2026-06-01T10:00:00Z", "failure"), 1),
        )
    ok = [{"name": "Checkout", "status": "completed", "conclusion": "success", "number": 1}]
    fail_steps = [
        {"name": "Checkout", "status": "completed", "conclusion": "success", "number": 1},
        {"name": "compile", "status": "completed", "conclusion": "failure", "number": 2},
    ]
    # run 401: a success job (must be ignored) + two failing jobs; first-by-id (5001) wins.
    await upsert_job(session, events.normalize_workflow_job(_job(5000, 401, "lint", "success", ok)))
    await upsert_job(
        session, events.normalize_workflow_job(_job(5001, 401, "build", "failure", fail_steps))
    )
    await upsert_job(
        session, events.normalize_workflow_job(_job(5002, 401, "test", "failure", fail_steps))
    )
    # run 402: one failing job whose steps have no failing step → step_name is None.
    await upsert_job(
        session, events.normalize_workflow_job(_job(5003, 402, "deploy", "failure", ok))
    )
    # run 403: no failing job at all → absent from the result.
    await upsert_job(session, events.normalize_workflow_job(_job(5004, 403, "docs", "success", ok)))
    await session.commit()

    counter.reset()
    failing = await _failing_steps_for(session, [401, 402, 403])

    assert counter.n == 1  # one batched query for all runs, not one per run
    assert failing[401] == ("build", "compile")  # first failing job by id, its first failing step
    assert failing[402] == ("deploy", None)  # failing job, but no failing step recorded
    assert 403 not in failing  # no failing job → no entry


async def test_failing_steps_empty_input_no_query(db):
    session, counter = db
    counter.reset()
    assert await _failing_steps_for(session, []) == {}
    assert counter.n == 0
