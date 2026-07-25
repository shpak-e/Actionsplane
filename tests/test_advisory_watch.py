"""Advisory watch (#7): GHSA parse + fleet match, poll pagination, upsert, scan, endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.audit import advisory_watch as aw
from actionsplane.audit.advisory_feed import (
    AdvisoryRecord,
    match_advisories,
    parse_advisories,
)
from actionsplane.db.base import Base
from actionsplane.db.models import Installation, Repo, WorkflowRelation
from actionsplane.db.repository import list_advisories, upsert_advisories

FIXED = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


# --- sample GHSA /advisories payload -------------------------------------------------------------

_GHSA_PAGE = [
    {
        "ghsa_id": "GHSA-aaaa-bbbb-cccc",
        "summary": "Command injection in changed-files",
        "severity": "high",
        "html_url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
        "published_at": "2025-03-14T10:00:00Z",
        "vulnerabilities": [
            {
                "package": {"ecosystem": "actions", "name": "tj-actions/changed-files"},
                "vulnerable_version_range": "< 46.0.1",
                "first_patched_version": "46.0.1",
            }
        ],
    },
    {
        "ghsa_id": "GHSA-dddd-eeee-ffff",
        "summary": "An npm-only issue",
        "severity": "critical",
        "html_url": "https://github.com/advisories/GHSA-dddd-eeee-ffff",
        "published_at": "2025-01-01T00:00:00Z",
        "vulnerabilities": [
            {"package": {"ecosystem": "npm", "name": "left-pad"}, "vulnerable_version_range": "*"}
        ],
    },
]


def test_parse_filters_to_actions_ecosystem():
    recs = parse_advisories(_GHSA_PAGE)
    assert len(recs) == 1  # the npm one is dropped
    r = recs[0]
    assert r.package == "tj-actions/changed-files"
    assert r.severity == "high" and r.first_patched == "46.0.1"
    assert r.published_at == datetime(2025, 3, 14, 10, 0, tzinfo=UTC)


def test_parse_handles_nested_patched_and_bad_dates():
    recs = parse_advisories(
        [
            {
                "ghsa_id": "GHSA-1",
                "severity": "LOW",
                "summary": "x",
                "published_at": "not-a-date",
                "vulnerabilities": [
                    {
                        "package": {"ecosystem": "actions", "name": "acme/act"},
                        "first_patched_version": {"identifier": "2.0.0"},
                    }
                ],
            }
        ]
    )
    assert recs[0].severity == "low"  # lowercased
    assert recs[0].first_patched == "2.0.0"  # nested identifier
    assert recs[0].published_at is None  # bad date tolerated
    assert recs[0].url == "https://github.com/advisories/GHSA-1"  # synthesized when missing


def test_match_advisories_is_case_insensitive():
    recs = parse_advisories(_GHSA_PAGE)
    hits = match_advisories(["actions/checkout@v4", "TJ-Actions/Changed-Files@v45"], recs)
    assert len(hits) == 1
    assert hits[0].advisory.package == "tj-actions/changed-files"
    assert hits[0].matched == "TJ-Actions/Changed-Files@v45"
    # no fleet action matches → no hits
    assert match_advisories(["actions/checkout@v4"], recs) == []


@pytest.mark.asyncio
async def test_poll_advisories_follows_pagination():
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if "page2" not in str(req.url):
            return httpx.Response(
                200,
                json=[_GHSA_PAGE[0]],
                headers={"Link": '<https://api.github.com/advisories?page2=1>; rel="next"'},
            )
        return httpx.Response(200, json=[_GHSA_PAGE[1]])  # no next link → stop

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        recs = await aw.poll_advisories(http, base_url="https://api.github.com", token="jwt")
    assert len(calls) == 2  # followed the next link once
    assert {r.package for r in recs} == {"tj-actions/changed-files"}  # npm dropped by parse


@pytest.mark.asyncio
async def test_poll_respects_max_pages():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_GHSA_PAGE[0]],
            headers={"Link": '<https://api.github.com/advisories?again=1>; rel="next"'},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        # infinite next-links, but max_pages caps it
        recs = await aw.poll_advisories(http, base_url="https://api.github.com", max_pages=2)
    assert len(recs) == 2  # one actions record per page, 2 pages


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(Installation(id=1, account_login="acme", account_type="User", installed_at=FIXED))
        s.add(Repo(id=10, installation_id=1, owner="acme", name="api", watched=True))
        s.add(Repo(id=11, installation_id=1, owner="acme", name="web", watched=True))
        s.add(
            WorkflowRelation(
                repo_id=10,
                path=".github/workflows/ci.yml",
                descriptor={"uses": ["tj-actions/changed-files@v45", "actions/checkout@v4"]},
            )
        )
        s.add(
            WorkflowRelation(
                repo_id=11,
                path=".github/workflows/release.yml",
                descriptor={"uses": ["tj-actions/changed-files@v44"]},
            )
        )
        await s.commit()
        yield s
    await engine.dispose()


async def test_upsert_advisories_is_idempotent(session):
    recs = parse_advisories(_GHSA_PAGE)
    rows = aw.advisory_rows(recs, fetched_at=FIXED)
    assert await upsert_advisories(session, rows) == 1
    await session.commit()
    # re-poll with an updated severity → same row updated, not duplicated
    rows[0]["severity"] = "critical"
    await upsert_advisories(session, rows)
    await session.commit()
    stored = await list_advisories(session)
    assert len(stored) == 1
    assert stored[0].severity == "critical"


async def test_scan_fleet_rolls_up_affected_repos(session):
    await upsert_advisories(
        session, aw.advisory_rows(parse_advisories(_GHSA_PAGE), fetched_at=FIXED)
    )
    await session.commit()
    report = await aw.scan_fleet(session, as_of=FIXED)
    assert report.advisories_known == 1
    assert report.workflows_scanned == 2
    assert len(report.entries) == 1
    e = report.entries[0]
    assert e.package == "tj-actions/changed-files"
    assert (e.workflows, e.repos) == (2, 2)  # both repos use it
    assert e.affected_repo_ids == [10, 11]
    assert e.first_patched == "46.0.1"


async def test_scan_fleet_empty_when_no_advisories(session):
    report = await aw.scan_fleet(session, as_of=FIXED)
    assert report.advisories_known == 0 and report.entries == []


async def test_scan_fleet_sorts_by_severity(session):
    # two advisories on actions the fleet uses; critical must rank before high
    payload = [
        _GHSA_PAGE[0],  # high, tj-actions/changed-files (used)
        {
            "ghsa_id": "GHSA-crit",
            "severity": "critical",
            "summary": "bad",
            "html_url": "https://github.com/advisories/GHSA-crit",
            "vulnerabilities": [{"package": {"ecosystem": "actions", "name": "actions/checkout"}}],
        },
    ]
    await upsert_advisories(session, aw.advisory_rows(parse_advisories(payload), fetched_at=FIXED))
    await session.commit()
    report = await aw.scan_fleet(session, as_of=FIXED)
    assert [e.severity for e in report.entries] == ["critical", "high"]


async def test_advisory_watch_endpoint(session):
    from actionsplane.api.app import advisory_watch_endpoint

    await upsert_advisories(
        session, aw.advisory_rows(parse_advisories(_GHSA_PAGE), fetched_at=FIXED)
    )
    await session.commit()
    out = await advisory_watch_endpoint(session=session)
    assert out.advisories_known == 1
    assert out.entries[0].package == "tj-actions/changed-files"


def test_advisory_rows_shape():
    rows = aw.advisory_rows(
        [
            AdvisoryRecord(
                ghsa_id="GHSA-x",
                package="acme/act",
                severity="high",
                summary="s",
                url="u",
                vulnerable_range="< 1",
                first_patched="1",
                published_at=FIXED,
            )
        ],
        fetched_at=FIXED,
    )
    assert rows[0]["ghsa_id"] == "GHSA-x" and rows[0]["fetched_at"] == FIXED
    # keys must match the Advisory columns the upsert writes
    assert set(rows[0]) == {
        "ghsa_id",
        "package",
        "severity",
        "summary",
        "url",
        "vulnerable_range",
        "first_patched",
        "published_at",
        "fetched_at",
    }
