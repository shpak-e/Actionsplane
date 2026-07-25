"""Evidence export (#8): assemble → render HTML/JSON → tamper-evident zip, and the API endpoint."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import (
    AuditFinding,
    Campaign,
    CampaignFinding,
    CampaignTarget,
    Installation,
    Repo,
)
from actionsplane.db.repository import campaign_finding_details
from actionsplane.reporting import evidence as ev

FIXED = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


def _finding(fid, repo_id, ftype, fp, *, resolved=False):
    return AuditFinding(
        id=fid,
        repo_id=repo_id,
        finding_type=ftype,
        severity="high",
        path=".github/workflows/ci.yml",
        ref=f"actions/checkout@v{fid}",
        message="unpinned action",
        fingerprint=fp,
        first_seen_at=FIXED,
        last_seen_at=FIXED,
        resolved_at=FIXED if resolved else None,
    )


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
            Campaign(
                id=1,
                name="Pin all actions",
                operation="pin-shas",
                created_at=FIXED,
                status="applied",
            )
        )
        s.add(
            CampaignTarget(
                id=1,
                campaign_id=1,
                repo_id=10,
                status="applied",
                pr_number=7,
                pr_url="https://github.com/acme/api/pull/7",
            )
        )
        s.add(
            CampaignTarget(
                id=2,
                campaign_id=1,
                repo_id=11,
                status="applied",
                pr_number=3,
                pr_url="https://github.com/acme/web/pull/3",
            )
        )
        # 3 targeted findings: 2 resolved (repo 10), 1 still open (repo 11)
        s.add(_finding(100, 10, "unpinned_action", "fp-a", resolved=True))
        s.add(_finding(101, 10, "unpinned_action", "fp-b", resolved=True))
        s.add(_finding(102, 11, "unpinned_action", "fp-c", resolved=False))
        for fp, rid in (("fp-a", 10), ("fp-b", 10), ("fp-c", 11)):
            s.add(
                CampaignFinding(
                    campaign_id=1,
                    repo_id=rid,
                    fingerprint=fp,
                    finding_type="unpinned_action",
                    severity="high",
                )
            )
        await s.commit()
        yield s
    await engine.dispose()


async def _bundle(session):
    campaign = await session.get(Campaign, 1)
    return await ev.build_campaign_evidence(session, campaign, generated_at=FIXED)


async def test_finding_details_join(session):
    rows = await campaign_finding_details(session, 1)
    assert len(rows) == 3
    # resolved-last ordering: the two open... actually open-first (resolved DESC on is-None)
    assert rows[0]["resolved"] is False  # the still-open one leads
    resolved = [r for r in rows if r["resolved"]]
    assert len(resolved) == 2
    assert {r["ref"] for r in rows} == {
        "actions/checkout@v100",
        "actions/checkout@v101",
        "actions/checkout@v102",
    }


async def test_bundle_counts_and_repo_names(session):
    b = await _bundle(session)
    assert (b.targeted, b.resolved) == (3, 2)
    assert b.resolved_pct == 67
    assert {t.repo for t in b.targets} == {"acme/api", "acme/web"}
    assert all(f.repo.startswith("acme/") for f in b.findings)


async def test_render_json_shape(session):
    b = await _bundle(session)
    doc = ev.render_json(b)
    assert doc["schema"] == "actionsplane.evidence/v1"
    assert doc["campaign"]["name"] == "Pin all actions"
    assert doc["verification"] == {
        "targeted": 3,
        "resolved": 2,
        "resolved_pct": 67,
        "by_type": {"unpinned_action": {"targeted": 3, "resolved": 2}},
    }
    assert len(doc["findings"]) == 3
    assert any(t["pr_url"] and t["pr_url"].endswith("/pull/7") for t in doc["targets"])


async def test_render_html_is_self_contained_and_escaped(session):
    campaign = await session.get(Campaign, 1)
    campaign.name = "Pin <script>alert(1)</script>"
    await session.commit()
    b = await _bundle(session)
    html = ev.render_html(b)
    assert html.lstrip().startswith("<!doctype html>")
    # self-contained: CSS is inline, nothing is fetched from outside the document
    assert "<style>" in html
    assert 'rel="stylesheet"' not in html
    assert "<script" not in html  # no scripts at all (the escaped name below is &lt;script&gt;)
    # user-controlled name is escaped, not injected
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    # the resolved count is rendered
    assert ">2<" in html and "/3 resolved" in html


async def test_zip_bundle_manifest_matches(session):
    b = await _bundle(session)
    data = ev.evidence_zip(b)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert names == {"report.html", "evidence.json", "manifest.txt"}
        html = zf.read("report.html")
        payload = zf.read("evidence.json")
        manifest = zf.read("manifest.txt").decode()
    # the manifest's SHA-256s must match the actual file bytes (tamper-evidence)
    assert hashlib.sha256(html).hexdigest() in manifest
    assert hashlib.sha256(payload).hexdigest() in manifest
    # evidence.json is valid JSON with the same counts
    assert json.loads(payload)["verification"]["resolved"] == 2


async def test_zip_is_deterministic(session):
    b = await _bundle(session)
    assert ev.evidence_zip(b) == ev.evidence_zip(b)  # same bundle → byte-identical archive


async def test_export_endpoint_returns_zip(session):
    from actionsplane.api.app import export_campaign_evidence_endpoint

    resp = await export_campaign_evidence_endpoint(1, session=session, actor="operate")
    assert resp.media_type == "application/zip"
    assert "campaign-1-evidence.zip" in resp.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(resp.body)) as zf:
        assert "report.html" in zf.namelist()


async def test_export_endpoint_404(session):
    from fastapi import HTTPException

    from actionsplane.api.app import export_campaign_evidence_endpoint

    with pytest.raises(HTTPException) as exc:
        await export_campaign_evidence_endpoint(999, session=session, actor="operate")
    assert exc.value.status_code == 404
