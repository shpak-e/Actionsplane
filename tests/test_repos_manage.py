"""Repo add/remove (watch toggle) — the fleet-management endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import Installation, Repo
from actionsplane.db.repository import (
    get_repo_by_owner_name,
    list_repos,
    set_repo_watched,
)


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
        s.add(
            Installation(
                id=1, account_login="acme", account_type="User", installed_at=datetime.now(UTC)
            )
        )
        s.add(Repo(id=10, installation_id=1, owner="acme", name="api", watched=True))
        await s.commit()
        yield s
    await engine.dispose()


async def test_owner_name_lookup_is_case_insensitive(session):
    assert (await get_repo_by_owner_name(session, "ACME", "API")).id == 10
    assert await get_repo_by_owner_name(session, "acme", "missing") is None


async def test_remove_then_readd_toggles_watched_and_list(session):
    from actionsplane.api.app import add_repo_endpoint, remove_repo_endpoint
    from actionsplane.api.schemas import RepoAddIn

    # remove → unwatched → drops out of the watched fleet list
    out = await remove_repo_endpoint(10, session=session, actor="tester")
    assert out == {"status": "removed", "repo_id": 10}
    assert [r.name for r in await list_repos(session, watched_only=True)] == []
    assert [r.name for r in await list_repos(session, watched_only=False)] == ["api"]

    # re-add an existing (unwatched) repo → just re-watched, no network fetch
    repo = await add_repo_endpoint(
        RepoAddIn(owner="acme", name="api"), session=session, actor="tester"
    )
    assert repo.id == 10 and repo.watched is True
    assert [r.name for r in await list_repos(session, watched_only=True)] == ["api"]


async def test_remove_missing_repo_404(session):
    from fastapi import HTTPException

    from actionsplane.api.app import remove_repo_endpoint

    with pytest.raises(HTTPException) as exc:
        await remove_repo_endpoint(999, session=session, actor="tester")
    assert exc.value.status_code == 404


async def test_set_repo_watched_persists(session):
    await set_repo_watched(session, 10, False)
    await session.commit()
    assert (await get_repo_by_owner_name(session, "acme", "api")).watched is False
