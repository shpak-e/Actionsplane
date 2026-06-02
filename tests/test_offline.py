"""Offline mode: repo-spec parsing, unauthenticated client, and the per-repo sync."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import Repo, WorkflowRun
from actionsplane.db.repository import upsert_installation
from actionsplane.github.client import GitHubClient
from actionsplane.offline.sync import OFFLINE_INSTALLATION_ID, parse_repo_spec, sync_repo


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
        await upsert_installation(
            s,
            {
                "id": OFFLINE_INSTALLATION_ID,
                "account_login": "offline",
                "account_type": "Organization",
                "installed_at": datetime.now(UTC),
            },
        )
        await s.commit()
        yield s
    await engine.dispose()


def test_parse_repo_spec():
    assert parse_repo_spec("octocat/hello") == ("octocat", "hello")
    assert parse_repo_spec("https://github.com/octocat/hello") == ("octocat", "hello")
    assert parse_repo_spec("https://github.com/octocat/hello.git/") == ("octocat", "hello")
    assert parse_repo_spec("   ") is None
    assert parse_repo_spec("not-a-repo") is None


@pytest.mark.asyncio
async def test_unauthenticated_client_sends_no_auth_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": 1, "default_branch": "main"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gh = GitHubClient(None, client=client, api_url="https://api.github.com")
        await gh.get_repo_meta("o", "r")

    assert captured["auth"] is None  # public read — no Authorization header


BARE_RUN = {
    "id": 9100,
    "workflow_id": 5,
    "run_number": 3,
    "head_branch": "main",
    "head_sha": "abc",
    "event": "push",
    "status": "completed",
    "conclusion": "success",
    "created_at": "2026-06-01T10:00:00Z",
    "run_started_at": "2026-06-01T10:00:05Z",
    "updated_at": "2026-06-01T10:02:00Z",
    "run_attempt": 1,
    "actor": {"login": "ci"},
}

UNPINNED_WF = (
    "name: ci\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
    "    steps:\n      - uses: actions/checkout@v3\n"
)


@pytest.mark.asyncio
async def test_sync_repo_persists_repo_runs_and_findings(session):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/acme/infra"):
            return httpx.Response(200, json={"id": 42, "default_branch": "main", "archived": False})
        if "/actions/runs" in url:
            return httpx.Response(200, json={"workflow_runs": [BARE_RUN]})
        if url.endswith("/contents/.github/workflows"):
            return httpx.Response(
                200,
                json=[{"type": "file", "name": "ci.yml", "path": ".github/workflows/ci.yml"}],
            )
        if url.endswith("/contents/.github/workflows/ci.yml"):
            content = base64.b64encode(UNPINNED_WF.encode()).decode()
            return httpx.Response(200, json={"content": content})
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gh = GitHubClient(None, client=client, api_url="https://api.github.com")
        runs, findings = await sync_repo(session, gh, "acme", "infra")

    assert runs == 1
    assert findings >= 1  # the unpinned actions/checkout@v3 raises at least one finding

    repo = await session.get(Repo, 42)
    assert repo is not None and repo.owner == "acme"
    assert await session.get(WorkflowRun, BARE_RUN["id"]) is not None
