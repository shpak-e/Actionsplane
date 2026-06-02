"""DB-backed regression tests for the scorecard + metrics API endpoints.

Both endpoints build their response from a `slots=True` dataclass. They originally did
`SomeOut(**obj.__dict__)`, which raises `AttributeError` at runtime because slotted dataclasses
have no `__dict__` — a bug that shipped because nothing exercised these endpoints against real
rows (caught only when the UI hit them against seeded Postgres). The fix is `asdict(obj)`. These
tests pin both endpoints against a seeded in-memory sqlite DB so the regression can't return.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.api.app import get_scorecard, get_workflow_metrics
from actionsplane.audit.findings import Finding
from actionsplane.db.base import Base
from actionsplane.db.repository import upsert_finding, upsert_repo, upsert_run
from actionsplane.ingestor import events
from actionsplane.models.enums import FindingType, Severity

WORKFLOW_ID = 900


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
        await upsert_finding(
            s,
            Finding(
                FindingType.UNPINNED_ACTION, Severity.HIGH, "pin it", "actions/checkout@v3"
            ).as_row(repo_id=1, path="ci.yml"),
        )
        run = {
            "id": 9001,
            "workflow_id": WORKFLOW_ID,
            "run_number": 1,
            "head_branch": "main",
            "head_sha": "abc",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-06-01T10:00:00Z",
            "run_started_at": "2026-06-01T10:00:05Z",
            "updated_at": "2026-06-01T10:02:00Z",
            "actor": {"login": "octocat"},
            "run_attempt": 1,
        }
        await upsert_run(s, events.normalize_run_object(run, repo_id=1))
        await s.commit()
        yield s
    await engine.dispose()


async def test_scorecard_endpoint_serializes(session):
    out = await get_scorecard(session=session)
    assert out.repos == 1
    assert out.open_findings == 1
    assert out.by_severity == {"high": 1}
    assert 0 <= out.score <= 100


async def test_metrics_endpoint_serializes(session):
    out = await get_workflow_metrics(workflow_id=WORKFLOW_ID, limit=500, session=session)
    assert out.runs == 1
    assert out.successes == 1
