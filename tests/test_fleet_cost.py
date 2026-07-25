"""Fleet cost/time rollup: per-workflow run-minutes aggregation + ranking (U1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import Installation, Repo, Workflow, WorkflowRun
from actionsplane.db.repository import fleet_cost_records


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


def _run(rid, repo_id, wf_id, *, dur_s, queue_s=5, conclusion="success", age_min=10):
    created = datetime.now(UTC) - timedelta(minutes=age_min)
    started = created + timedelta(seconds=queue_s)
    completed = started + timedelta(seconds=dur_s)
    return WorkflowRun(
        id=rid,
        repo_id=repo_id,
        workflow_id=wf_id,
        run_number=rid,
        conclusion=conclusion,
        created_at=created,
        started_at=started,
        completed_at=completed,
    )


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(
            Installation(
                id=1, account_login="acme", account_type="User", installed_at=datetime.now(UTC)
            )
        )
        s.add(Repo(id=10, installation_id=1, owner="acme", name="api", watched=True))
        s.add(Workflow(id=100, repo_id=10, path=".github/workflows/ci.yml", name="CI"))
        s.add(Workflow(id=101, repo_id=10, path=".github/workflows/deploy.yml", name="Deploy"))
        # CI: 2 runs of 60s = 120s total. Deploy: 1 run of 600s = 600s (more expensive).
        s.add(_run(1, 10, 100, dur_s=60))
        s.add(_run(2, 10, 100, dur_s=60, conclusion="failure"))
        s.add(_run(3, 10, 101, dur_s=600))
        await s.commit()
        yield s
    await engine.dispose()


async def test_fleet_cost_records_derives_durations(session):
    recs = await fleet_cost_records(session)
    assert len(recs) == 3
    by_wf = {}
    for r in recs:
        by_wf.setdefault(r["workflow_id"], []).append(r)
    assert sum(x["duration_s"] for x in by_wf[100]) == 120
    assert by_wf[101][0]["duration_s"] == 600


async def test_fleet_endpoint_ranks_by_compute(session):
    from actionsplane.api import app as api_app

    api_app._fleet_cost_cache.clear()  # module-global TTL cache — isolate from other tests
    out = await api_app.fleet_cost_metrics(days=30, limit=50, session=session)
    assert out.window_days == 30
    assert out.total_minutes == pytest.approx((120 + 600) / 60, abs=0.05)
    # Deploy (600s) ranks above CI (120s)
    assert [r.name for r in out.rows] == ["Deploy", "CI"]
    ci = next(r for r in out.rows if r.name == "CI")
    assert ci.runs == 2 and ci.failures == 1 and ci.repo == "acme/api"
    assert ci.total_minutes == pytest.approx(2.0, abs=0.05)


async def test_fleet_cost_window_excludes_old_runs(session):
    # Everything is ~10 min old; a 'since' set in the future catches nothing.
    since = datetime.now(UTC) + timedelta(minutes=1)
    assert await fleet_cost_records(session, since=since) == []
