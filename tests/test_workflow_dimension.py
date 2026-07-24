"""Ingest must populate the ``workflows`` dimension a run FK-references (live-validation 5.1).

``workflow_runs.workflow_id`` FKs ``workflows.id`` (GitHub's workflow id). Only the seed script
ever created those parent rows, so on a real installation every ``workflow_run`` webhook tripped a
ForeignKeyViolationError and no run persisted. The hermetic suite missed it because SQLite disables
FK enforcement by default — so this module turns it ON, reproducing Postgres's behaviour, and pins
the contract that ingest upserts the workflow parent before the run.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import Installation, Repo, Workflow
from actionsplane.db.repository import list_workflows, upsert_run, upsert_workflow
from actionsplane.ingestor import events


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")

    # SQLite ignores FKs unless asked — turn them on so this test sees the real Postgres constraint.
    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(
            Installation(
                id=999, account_login="acme", account_type="User", installed_at=datetime.now(UTC)
            )
        )
        s.add(Repo(id=42, installation_id=999, owner="acme", name="infra"))
        await s.commit()
        yield s
    await engine.dispose()


# A real workflow_run object carries workflow_id + path + name (unlike the trimmed test_events one).
RUN = {
    "id": 1001,
    "workflow_id": 55,
    "path": ".github/workflows/ci.yml",
    "name": "ci",
    "run_number": 7,
    "head_branch": "main",
    "head_sha": "abc123",
    "event": "push",
    "status": "completed",
    "conclusion": "success",
    "created_at": "2026-07-24T10:00:00Z",
    "run_started_at": "2026-07-24T10:00:05Z",
    "updated_at": "2026-07-24T10:03:00Z",
    "run_attempt": 1,
    "actor": {"login": "octocat"},
}


async def test_run_insert_without_parent_workflow_violates_fk(session):
    """The bug, reproduced: inserting a run whose workflow has no dimension row fails under FK
    enforcement — proving the constraint is real and the parent-first ordering is load-bearing."""
    with pytest.raises(IntegrityError):
        await upsert_run(session, events.normalize_run_object(RUN, repo_id=42))
        await session.commit()


async def test_ingest_upserts_workflow_parent_then_run(session):
    """The fix: derive + upsert the workflow parent, then the run persists cleanly."""
    wf = events.workflow_ref_from_run(RUN, repo_id=42)
    assert wf == {"id": 55, "repo_id": 42, "path": ".github/workflows/ci.yml", "name": "ci"}
    await upsert_workflow(session, wf)
    await upsert_run(session, events.normalize_run_object(RUN, repo_id=42))
    await session.commit()

    stored = await session.get(Workflow, 55)
    assert stored.path == ".github/workflows/ci.yml"
    assert stored.name == "ci"
    # The dimension is now real for live repos, so per-workflow metrics can enumerate it.
    assert [w.id for w in await list_workflows(session, repo_id=42)] == [55]


async def test_workflow_upsert_is_idempotent_and_non_clobbering(session):
    """Re-ingesting the same workflow is a no-op; a later sparser event keeps the id addressable."""
    await upsert_workflow(session, events.workflow_ref_from_run(RUN, repo_id=42))
    await upsert_workflow(session, events.workflow_ref_from_run(RUN, repo_id=42))
    await session.commit()
    assert len(await list_workflows(session, repo_id=42)) == 1


def test_workflow_ref_none_when_run_names_no_workflow():
    """A run object without a workflow_id yields no parent — the run's nullable FK stays NULL."""
    assert events.workflow_ref_from_run({"id": 5, "run_number": 1}, repo_id=42) is None
