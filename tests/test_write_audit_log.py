"""Write-operation audit trail (Phase 5.2): append helper, newest-first listing, API endpoint.

DB-backed on in-memory sqlite (JSONB→JSON shim) like the other repository tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.api.app import get_audit_log
from actionsplane.db.base import Base
from actionsplane.db.repository import list_write_audit, record_write_audit


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
        yield s
    await engine.dispose()


async def test_record_and_list_newest_first(session):
    await record_write_audit(
        session, actor="operate", action="campaign.create", target="campaign:1"
    )
    await record_write_audit(
        session,
        actor="operate",
        action="campaign.apply",
        target="campaign:1",
        detail={"pr_urls": ["https://github.com/acme/infra/pull/7"]},
    )
    await record_write_audit(session, actor="worker", action="sarif.upload", target="acme/infra")
    await session.commit()

    rows = await list_write_audit(session)
    assert [r.action for r in rows] == ["sarif.upload", "campaign.apply", "campaign.create"]
    assert rows[1].detail == {"pr_urls": ["https://github.com/acme/infra/pull/7"]}
    assert rows[0].actor == "worker"
    assert all(r.occurred_at is not None for r in rows)


async def test_list_pagination(session):
    for i in range(5):
        await record_write_audit(session, actor="operate", action="run.rerun", target=f"run:{i}")
    await session.commit()

    page1 = await list_write_audit(session, limit=2, offset=0)
    page2 = await list_write_audit(session, limit=2, offset=2)
    assert [r.target for r in page1] == ["run:4", "run:3"]
    assert [r.target for r in page2] == ["run:2", "run:1"]


async def test_audit_log_endpoint_serializes(session):
    await record_write_audit(
        session, actor="operate", action="run.rerun", target="run:9", detail={"note": "x"}
    )
    await session.commit()

    out = await get_audit_log(limit=10, offset=0, session=session, actor="operate")
    assert len(out) == 1
    assert out[0].action == "run.rerun"
    assert out[0].target == "run:9"
    assert out[0].detail == {"note": "x"}
