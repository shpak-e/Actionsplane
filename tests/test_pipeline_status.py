"""DB-backed: Pipelines nodes carry latest-run status + failed step, and jobs surface their steps.

Seeds a repo with one workflow whose latest run failed at a specific step, plus the matching
workflow_relation, then drives the real API handlers against sqlite (JSONB→JSON shim).
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.api import app as api_app
from actionsplane.api.app import get_jobs, get_pipelines
from actionsplane.db.base import Base
from actionsplane.db.models import Workflow
from actionsplane.db.repository import (
    upsert_job,
    upsert_repo,
    upsert_run,
    upsert_workflow_relation,
)
from actionsplane.ingestor import events

WORKFLOW_ID = 4242
RUN_ID = 99001


@pytest.fixture(autouse=True)
def _reset_pipelines_cache():
    """The /pipelines TTL cache is process-global; clear it so tests don't see a stale graph."""
    api_app._pipelines_cache["value"] = None
    api_app._pipelines_cache["at"] = 0.0


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


def _descriptor():
    return {
        "name": "Deploy",
        "triggers": ["workflow_run"],
        "workflow_run_upstreams": [],
        "is_reusable": False,
        "accepts_dispatch": False,
        "dispatch_types": [],
        "calls": [],
        "emits": [],
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
        await s.merge(
            Workflow(id=WORKFLOW_ID, repo_id=1, path=".github/workflows/deploy.yml", name="Deploy")
        )
        run = {
            "id": RUN_ID,
            "workflow_id": WORKFLOW_ID,
            "run_number": 12,
            "head_branch": "main",
            "head_sha": "abc",
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-06-01T10:00:00Z",
            "run_started_at": "2026-06-01T10:00:05Z",
            "updated_at": "2026-06-01T10:02:00Z",
            "actor": {"login": "octocat"},
            "run_attempt": 1,
        }
        await upsert_run(s, events.normalize_run_object(run, repo_id=1))
        job = {
            "id": 5001,
            "run_id": RUN_ID,
            "name": "deploy-production",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-06-01T10:00:05Z",
            "completed_at": "2026-06-01T10:02:00Z",
            "steps": [
                {"name": "Checkout", "status": "completed", "conclusion": "success", "number": 1},
                {
                    "name": "terraform apply",
                    "status": "completed",
                    "conclusion": "failure",
                    "number": 2,
                },
                {"name": "Notify", "status": "completed", "conclusion": "skipped", "number": 3},
            ],
        }
        await upsert_job(s, events.normalize_workflow_job({"workflow_job": job}))
        await upsert_workflow_relation(
            s,
            repo_id=1,
            path=".github/workflows/deploy.yml",
            name="Deploy",
            descriptor=_descriptor(),
        )
        await s.commit()
        yield s
    await engine.dispose()


async def test_pipeline_node_reports_failed_step(session):
    graph = await get_pipelines(session=session)
    node = next(n for n in graph.nodes if n.name == "Deploy")
    assert node.conclusion == "failure"
    assert node.run_id == RUN_ID and node.run_number == 12
    assert node.failed_job == "deploy-production"
    assert node.failed_step == "terraform apply"  # the precise failing step


async def test_jobs_endpoint_surfaces_steps(session):
    jobs = await get_jobs(run_id=RUN_ID, session=session)
    assert len(jobs) == 1
    steps = jobs[0].steps
    assert [s.name for s in steps] == ["Checkout", "terraform apply", "Notify"]
    failed = next(s for s in steps if s.conclusion == "failure")
    assert failed.name == "terraform apply"


async def test_pipelines_are_cached_within_ttl(monkeypatch):
    """M5: a second call inside the TTL returns the cached graph without rebuilding (no DB hit)."""
    calls = {"n": 0}
    sentinel = object()

    async def fake_build(_session):
        calls["n"] += 1
        return sentinel

    monkeypatch.setattr(api_app, "_build_pipelines", fake_build)
    first = await get_pipelines(session=None)  # session unused on the cache path
    second = await get_pipelines(session=None)
    assert calls["n"] == 1  # built once, served from cache the second time
    assert first is second is sentinel
