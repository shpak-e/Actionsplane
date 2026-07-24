"""W8 — Deprecation Radar: the pure feed matcher, the persisted facts, and the fleet scan."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.audit.deprecation_feed import DeprecationEntry, match_facts
from actionsplane.audit.deprecation_radar import scan_fleet
from actionsplane.audit.parser import parse_workflow
from actionsplane.db.base import Base
from actionsplane.db.models import Installation, Repo
from actionsplane.db.repository import upsert_workflow_relation
from actionsplane.relations import extract_relations

FEED = (
    DeprecationEntry(
        "ubuntu-22.04-retirement",
        "runner-label",
        "ubuntu-22.04",
        "ubuntu-24.04",
        date(2026, 9, 17),
        "ref",
        "swap-runs-on",
    ),
    DeprecationEntry(
        "upload-artifact-v3",
        "action-version",
        "actions/upload-artifact@v3",
        "actions/upload-artifact@v4",
        date(2025, 1, 30),
        "ref",
        "bump-action-version",
    ),
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


def test_match_facts_runner_label_and_action_version():
    hits = match_facts(
        ["ubuntu-22.04"], ["actions/checkout@v4", "actions/upload-artifact@v3"], FEED
    )
    ids = {h.entry.id for h in hits}
    assert ids == {"ubuntu-22.04-retirement", "upload-artifact-v3"}


def test_match_facts_ignores_current_versions_and_labels():
    hits = match_facts(["ubuntu-24.04"], ["actions/upload-artifact@v4"], FEED)
    assert hits == []


def test_extract_relations_persists_radar_facts():
    """The audit persists runner labels + uses on the relation descriptor, so the scan needs no
    re-fetch."""
    wf = parse_workflow(
        "name: ci\non: [push]\njobs:\n"
        "  build:\n    runs-on: ubuntu-22.04\n    steps:\n"
        "      - uses: actions/upload-artifact@v3\n",
        "ci.yml",
    )
    desc = extract_relations(wf)
    assert desc["runs_on"] == ["ubuntu-22.04"]
    assert "actions/upload-artifact@v3" in desc["uses"]


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
        s.add(Repo(id=10, installation_id=1, owner="acme", name="a"))
        s.add(Repo(id=11, installation_id=1, owner="acme", name="b"))
        await s.commit()
        yield s
    await engine.dispose()


async def test_scan_fleet_inventory_with_countdown(session):
    # two repos on ubuntu-22.04; one also on the dead artifact v3.
    await upsert_workflow_relation(
        session,
        repo_id=10,
        path=".github/workflows/ci.yml",
        name="ci",
        descriptor={"runs_on": ["ubuntu-22.04"], "uses": ["actions/upload-artifact@v3"]},
    )
    await upsert_workflow_relation(
        session,
        repo_id=11,
        path=".github/workflows/ci.yml",
        name="ci",
        descriptor={"runs_on": ["ubuntu-22.04"], "uses": ["actions/checkout@v4"]},
    )
    await session.commit()

    report = await scan_fleet(session, as_of=date(2026, 8, 18), feed=FEED)
    assert report.workflows_scanned == 2
    by_id = {e.id: e for e in report.entries}

    ubuntu = by_id["ubuntu-22.04-retirement"]
    assert ubuntu.workflows == 2 and ubuntu.repos == 2
    assert ubuntu.days_until == 30 and ubuntu.status == "urgent"  # 2026-08-18 -> 2026-09-17
    assert ubuntu.fixable_repo_ids == [10, 11]

    artifact = by_id["upload-artifact-v3"]
    assert artifact.repos == 1 and artifact.status == "overdue"  # deadline already passed
    assert artifact.fixable_repo_ids == [10]

    # Overdue sorts before urgent.
    assert report.entries[0].id == "upload-artifact-v3"


async def test_scan_fleet_omits_unhit_entries(session):
    await upsert_workflow_relation(
        session,
        repo_id=10,
        path=".github/workflows/ci.yml",
        name="ci",
        descriptor={"runs_on": ["ubuntu-24.04"], "uses": ["actions/checkout@v4"]},
    )
    await session.commit()
    report = await scan_fleet(session, as_of=date(2026, 8, 18), feed=FEED)
    assert report.entries == []  # nothing in the fleet hits the feed
