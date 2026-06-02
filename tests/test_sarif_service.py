"""End-to-end SARIF orchestration: stored findings -> SARIF doc -> Code Scanning upload.

DB-backed (sqlite + JSONB->JSON shim) with a MockTransport GitHub client, so the whole
read-findings -> resolve-head -> upload path is exercised without a real App or network.
"""

from __future__ import annotations

import base64
import gzip
import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.audit.findings import Finding
from actionsplane.audit.sarif_service import upload_repo_sarif
from actionsplane.db.base import Base
from actionsplane.db.models import AuditFinding, Repo
from actionsplane.db.repository import upsert_finding, upsert_repo
from actionsplane.github.client import GitHubClient
from actionsplane.models.enums import FindingType, Severity


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
        await upsert_finding(
            s,
            Finding(FindingType.MISSING_PERMISSIONS, Severity.MEDIUM, "set perms").as_row(
                repo_id=1, path="ci.yml"
            ),
        )
        await s.commit()
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_upload_repo_sarif_builds_and_uploads(session):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and "/git/ref/heads/main" in url:
            return httpx.Response(200, json={"object": {"sha": "f" * 40}})
        if request.method == "POST" and url.endswith("/code-scanning/sarifs"):
            captured["body"] = json.loads(request.content)
            return httpx.Response(202, json={"id": 7, "url": "https://api.github.com/x/7"})
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        repo = await session.get(Repo, 1)
        result = await upload_repo_sarif(session, gh, repo)

    assert result == {"id": 7, "url": "https://api.github.com/x/7"}
    body = captured["body"]
    assert body["commit_sha"] == "f" * 40  # resolved from the default branch head
    assert body["ref"] == "refs/heads/main"
    # the uploaded SARIF carries both stored findings, decoded round-trip
    doc = json.loads(gzip.decompress(base64.b64decode(body["sarif"])))
    rule_ids = {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]}
    assert rule_ids == {"unpinned_action", "missing_permissions"}
    assert len(doc["runs"][0]["results"]) == 2


@pytest.mark.asyncio
async def test_upload_repo_sarif_empty_findings_still_uploads(session):
    """A repo with zero open findings still uploads (empty results) so stale alerts get closed."""
    # resolve all findings first so open_findings returns nothing
    await session.execute(update(AuditFinding).values(resolved_at=datetime.now(UTC)))
    await session.commit()

    posted = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and "/git/ref/heads/main" in url:
            return httpx.Response(200, json={"object": {"sha": "a" * 40}})
        if request.method == "POST" and url.endswith("/code-scanning/sarifs"):
            posted["body"] = json.loads(request.content)
            return httpx.Response(202, json={"id": 1, "url": "u"})
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        repo = await session.get(Repo, 1)
        await upload_repo_sarif(session, gh, repo)

    doc = json.loads(gzip.decompress(base64.b64decode(posted["body"]["sarif"])))
    assert doc["runs"][0]["results"] == []  # empty analysis uploaded on purpose
