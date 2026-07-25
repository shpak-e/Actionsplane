"""Verified-closed loop (D2/P1): snapshot targeted findings → count resolved after re-audit."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import AuditFinding, Campaign, CampaignTarget, Installation, Repo
from actionsplane.db.repository import (
    campaign_verification,
    campaign_verification_counts,
    snapshot_campaign_findings,
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


def _finding(fid: int, repo_id: int, ftype: str, fp: str, resolved: bool = False) -> AuditFinding:
    now = datetime.now(UTC)
    return AuditFinding(
        id=fid,
        repo_id=repo_id,
        finding_type=ftype,
        severity="high",
        path=".github/workflows/ci.yml",
        ref=f"ref-{fid}",
        message="x",
        fingerprint=fp,
        first_seen_at=now,
        last_seen_at=now,
        resolved_at=now if resolved else None,
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
        s.add(Repo(id=11, installation_id=1, owner="acme", name="web", watched=True))
        s.add(
            Campaign(
                id=1,
                name="pin",
                operation="pin-shas",
                created_at=datetime.now(UTC),
                status="applied",
            )
        )
        s.add(CampaignTarget(id=1, campaign_id=1, repo_id=10, status="applied"))
        s.add(CampaignTarget(id=2, campaign_id=1, repo_id=11, status="applied"))
        # 3 open unpinned findings (2 in repo 10, 1 in repo 11) + a permissions one it won't target
        s.add(_finding(100, 10, "unpinned_action", "fp-a"))
        s.add(_finding(101, 10, "unpinned_action", "fp-b"))
        s.add(_finding(102, 11, "unpinned_action", "fp-c"))
        s.add(_finding(103, 10, "missing_permissions", "fp-perm"))
        await s.commit()
        yield s
    await engine.dispose()


async def test_snapshot_captures_only_targeted_types(session):
    n = await snapshot_campaign_findings(
        session, campaign_id=1, repo_ids=[10, 11], finding_types={"unpinned_action"}
    )
    assert n == 3  # the 3 unpinned findings, not the permissions one
    await session.commit()


async def test_snapshot_noop_without_types(session):
    assert (
        await snapshot_campaign_findings(session, campaign_id=1, repo_ids=[10], finding_types=set())
        == 0
    )


async def test_verification_counts_resolved_after_reaudit(session):
    await snapshot_campaign_findings(
        session, campaign_id=1, repo_ids=[10, 11], finding_types={"unpinned_action"}
    )
    await session.commit()

    # Nothing resolved yet.
    v0 = await campaign_verification(session, 1)
    assert (v0["targeted"], v0["resolved"]) == (3, 0)

    # Simulate a re-audit resolving two of the three targeted findings.
    for fid in (100, 101):
        f = await session.get(AuditFinding, fid)
        f.resolved_at = datetime.now(UTC)
    await session.commit()

    v1 = await campaign_verification(session, 1)
    assert (v1["targeted"], v1["resolved"]) == (3, 2)
    # per-repo: repo 10 had 2 targeted, both resolved; repo 11 had 1, none resolved
    by_repo = {r["repo_id"]: r for r in v1["by_repo"]}
    assert (by_repo[10]["targeted"], by_repo[10]["resolved"]) == (2, 2)
    assert (by_repo[11]["targeted"], by_repo[11]["resolved"]) == (1, 0)


async def test_verification_counts_batched(session):
    await snapshot_campaign_findings(
        session, campaign_id=1, repo_ids=[10, 11], finding_types={"unpinned_action"}
    )
    f = await session.get(AuditFinding, 102)
    f.resolved_at = datetime.now(UTC)
    await session.commit()

    counts = await campaign_verification_counts(session, [1])
    assert counts[1] == (3, 1)
    assert await campaign_verification_counts(session, []) == {}
