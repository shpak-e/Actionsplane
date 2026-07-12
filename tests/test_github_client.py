"""Tests for the GitHub REST client and the shared run normalizer."""

from __future__ import annotations

import httpx
import pytest

from actionsplane.github.client import GitHubClient
from actionsplane.ingestor import events

BARE_RUN = {
    "id": 5001,
    "workflow_id": 77,
    "run_number": 12,
    "head_branch": "main",
    "head_sha": "deadbeef",
    "event": "push",
    "status": "completed",
    "conclusion": "success",
    "created_at": "2026-05-24T09:00:00Z",
    "run_started_at": "2026-05-24T09:00:03Z",
    "updated_at": "2026-05-24T09:05:00Z",
    "run_attempt": 1,
    "actor": {"login": "ci-bot"},
}


def test_normalize_run_object():
    row = events.normalize_run_object(BARE_RUN, repo_id=42)
    assert row["id"] == 5001
    assert row["repo_id"] == 42
    assert row["workflow_id"] == 77
    assert row["actor"] == "ci-bot"
    assert row["completed_at"] is not None


def test_normalize_workflow_run_delegates():
    payload = {"repository": {"id": 42}, "workflow_run": BARE_RUN}
    assert events.normalize_workflow_run(payload) == events.normalize_run_object(BARE_RUN, 42)


@pytest.mark.asyncio
async def test_list_workflow_runs():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"total_count": 1, "workflow_runs": [BARE_RUN]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("ghs_tok", client=client, api_url="https://api.github.com")
        runs = await gh.list_workflow_runs("acme", "infra", per_page=10)

    assert len(runs) == 1
    assert runs[0]["id"] == 5001
    assert captured["url"] == "https://api.github.com/repos/acme/infra/actions/runs?per_page=10"
    assert captured["auth"] == "Bearer ghs_tok"


@pytest.mark.asyncio
async def test_list_workflow_runs_paginates():
    """Walk rel=next across two pages, then stop on the page with no Link header."""
    base = "https://api.github.com/repos/acme/infra/actions/runs"

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        if page is None:  # first page → point at page 2 via Link
            return httpx.Response(
                200,
                json={"total_count": 2, "workflow_runs": [{**BARE_RUN, "id": 1}]},
                headers={"Link": f'<{base}?per_page=100&page=2>; rel="next"'},
            )
        # second (last) page: no Link header → pagination stops here
        return httpx.Response(200, json={"workflow_runs": [{**BARE_RUN, "id": 2}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        runs = await gh.list_workflow_runs("acme", "infra")

    assert [r["id"] for r in runs] == [1, 2]  # both pages concatenated, newest-first preserved


@pytest.mark.asyncio
async def test_list_workflow_runs_respects_max_runs():
    """A repo that keeps offering a next page is bounded by max_runs (no unbounded walk)."""
    base = "https://api.github.com/repos/acme/infra/actions/runs"
    pages_served = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal pages_served
        pages_served += 1
        n = int(request.url.params.get("page") or 1)
        return httpx.Response(
            200,
            json={"workflow_runs": [{**BARE_RUN, "id": n}, {**BARE_RUN, "id": n * 100}]},
            headers={"Link": f'<{base}?per_page=100&page={n + 1}>; rel="next"'},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        runs = await gh.list_workflow_runs("acme", "infra", per_page=2, max_runs=3)

    assert len(runs) == 3  # capped, not the full infinite stream
    assert pages_served == 2  # stopped as soon as the cap was reached


@pytest.mark.asyncio
async def test_cross_origin_pagination_link_is_ignored():
    """A hostile rel=next pointing off the API host is NOT followed, so the Authorization header
    can't be exfiltrated to an attacker origin (review 3, N3)."""
    evil = "https://evil.example.com/steal"
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(
            200, json={"workflow_runs": [BARE_RUN]}, headers={"Link": f'<{evil}>; rel="next"'}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        runs = await gh.list_workflow_runs("acme", "infra")

    assert len(runs) == 1  # walk stopped after the first page
    assert "evil.example.com" not in hosts  # attacker origin never contacted


@pytest.mark.asyncio
async def test_list_workflow_runs_passes_created_filter():
    """The reconcile window (review 3, 4b) rides GitHub's server-side ``created`` filter."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"workflow_runs": [BARE_RUN]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        await gh.list_workflow_runs("acme", "infra", created=">=2026-07-01", max_runs=100)

    assert captured["params"].get("created") == ">=2026-07-01"


@pytest.mark.asyncio
async def test_etag_conditional_request_reuses_cached_body():
    """A reused client sends If-None-Match on the 2nd call; the 304 returns the cached body — the
    ETag cache survives across calls, which is what per-installation client reuse buys (4c)."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.headers.get("if-none-match") == '"etag-1"':
            return httpx.Response(304, headers={"ETag": '"etag-1"'})
        return httpx.Response(200, json={"workflow_runs": [BARE_RUN]}, headers={"ETag": '"etag-1"'})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        first = await gh.list_workflow_runs("acme", "infra")
        second = await gh.list_workflow_runs("acme", "infra")  # same client → cache hit → 304

    assert calls["n"] == 2
    assert [r["id"] for r in first] == [r["id"] for r in second]  # 304 served the cached page


@pytest.mark.asyncio
async def test_list_and_get_workflow_files():
    import base64

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/contents/.github/workflows"):
            return httpx.Response(
                200,
                json=[
                    {"type": "file", "name": "ci.yml", "path": ".github/workflows/ci.yml"},
                    {"type": "file", "name": "README.md", "path": ".github/workflows/README.md"},
                    {"type": "dir", "name": "sub", "path": ".github/workflows/sub"},
                ],
            )
        if url.endswith("/contents/.github/workflows/ci.yml"):
            content = base64.b64encode(b"name: ci\non: [push]\n").decode()
            return httpx.Response(200, json={"content": content, "encoding": "base64"})
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        files = await gh.list_workflow_files("acme", "infra")
        assert files == [".github/workflows/ci.yml"]  # only .yml/.yaml files
        text = await gh.get_file_text("acme", "infra", ".github/workflows/ci.yml")
        assert "name: ci" in text


@pytest.mark.asyncio
async def test_list_workflow_files_missing_dir():
    transport = httpx.MockTransport(lambda r: httpx.Response(404, json={"message": "Not Found"}))
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        assert await gh.list_workflow_files("acme", "norepo") == []


@pytest.mark.asyncio
async def test_get_file_text_rejects_traversal():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"content": ""}))
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        with pytest.raises(ValueError):
            await gh.get_file_text("acme", "infra", "../../etc/passwd")


@pytest.mark.asyncio
async def test_write_flow_methods():
    """Exercise the write path: resolve sha, get ref, create branch, put file, open PR."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        url = str(request.url)
        if request.method == "GET" and "/commits/" in url:
            return httpx.Response(200, text="d" * 40)
        if request.method == "GET" and "/git/ref/heads/" in url:
            return httpx.Response(200, json={"object": {"sha": "base" + "0" * 36}})
        if request.method == "POST" and url.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if request.method == "PUT" and "/contents/" in url:
            return httpx.Response(200, json={"commit": {"sha": "x"}})
        if request.method == "POST" and url.endswith("/pulls"):
            return httpx.Response(
                201, json={"number": 7, "html_url": "https://github.com/acme/infra/pull/7"}
            )
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        assert await gh.get_commit_sha("acme", "infra", "v4") == "d" * 40
        base = await gh.get_ref_sha("acme", "infra", "main")
        await gh.create_branch("acme", "infra", "actionsplane/pin-1", base)
        await gh.put_file(
            "acme",
            "infra",
            ".github/workflows/ci.yml",
            text="name: ci\n",
            message="pin",
            branch="actionsplane/pin-1",
            sha="blob1",
        )
        pr = await gh.create_pull_request(
            "acme", "infra", head="actionsplane/pin-1", base="main", title="Pin", body="…"
        )
    assert pr == {"number": 7, "html_url": "https://github.com/acme/infra/pull/7"}
    assert ("POST", "https://api.github.com/repos/acme/infra/pulls") in calls
