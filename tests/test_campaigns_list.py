"""GET /campaigns list endpoint + per-campaign target-count aggregation (UI-F1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import Campaign, CampaignTarget, Installation, Repo
from actionsplane.db.repository import campaign_target_counts, list_campaigns


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
        s.add(Repo(id=11, installation_id=1, owner="acme", name="web", watched=True))
        now = datetime.now(UTC)
        # older campaign: 2 targets, 1 opened PR, 1 errored
        s.add(
            Campaign(
                id=1,
                name="pin actions",
                operation="pin-shas",
                created_at=now - timedelta(hours=1),
                status="applied",
            )
        )
        s.add(
            CampaignTarget(
                id=1,
                campaign_id=1,
                repo_id=10,
                status="applied",
                pr_number=42,
                pr_url="https://gh/pr/42",
            )
        )
        s.add(CampaignTarget(id=2, campaign_id=1, repo_id=11, status="error", error="boom"))
        # newer campaign: 1 target, dry-run ready, no PR yet
        s.add(
            Campaign(
                id=2,
                name="set perms",
                operation="set-permissions",
                created_at=now,
                status="pending",
            )
        )
        s.add(
            CampaignTarget(
                id=3, campaign_id=2, repo_id=10, status="dry-run-ok", diff_preview="--- a\n+++ b"
            )
        )
        await s.commit()
        yield s
    await engine.dispose()


async def test_list_campaigns_newest_first(session):
    campaigns = await list_campaigns(session)
    assert [c.id for c in campaigns] == [2, 1]  # newest created_at first


async def test_target_counts_group_by(session):
    counts = await campaign_target_counts(session, [1, 2])
    assert counts[1] == (2, 1, 1)  # total, opened (pr_number set), errored
    assert counts[2] == (1, 0, 0)


async def test_target_counts_empty_input(session):
    assert await campaign_target_counts(session, []) == {}


async def test_list_endpoint_returns_summaries(session):
    from actionsplane.api.app import list_campaigns_endpoint

    out = await list_campaigns_endpoint(limit=100, session=session)
    assert [c.id for c in out] == [2, 1]
    by_id = {c.id: c for c in out}
    assert (by_id[1].targets, by_id[1].opened, by_id[1].errored) == (2, 1, 1)
    assert (by_id[2].targets, by_id[2].opened, by_id[2].errored) == (1, 0, 0)
    assert by_id[2].operation == "set-permissions"
