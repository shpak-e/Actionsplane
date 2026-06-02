"""End-to-end (MockTransport) tests for the campaign dry-run + PR service."""

from __future__ import annotations

import base64

import httpx
import pytest

from actionsplane.executor.service import dry_run_repo, open_pr_for_edits
from actionsplane.github.client import GitHubClient

WF = (
    "name: ci\n"
    "on: [push]\n"
    "jobs:\n"
    "  build:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "      - run: make test\n"
)
SHA = "a" * 40


def _handler(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        url = str(request.url)
        if request.method == "GET" and url.endswith("/contents/.github/workflows"):
            return httpx.Response(
                200,
                json=[{"type": "file", "name": "ci.yml", "path": ".github/workflows/ci.yml"}],
            )
        if request.method == "GET" and "/contents/.github/workflows/ci.yml" in url:
            return httpx.Response(
                200, json={"content": base64.b64encode(WF.encode()).decode(), "sha": "blob1"}
            )
        if request.method == "GET" and "/commits/" in url:
            return httpx.Response(200, text=SHA)
        if request.method == "GET" and "/git/ref/heads/" in url:
            return httpx.Response(200, json={"object": {"sha": "base000"}})
        if request.method == "POST" and url.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if request.method == "PUT" and "/contents/" in url:
            return httpx.Response(200, json={"commit": {"sha": "c1"}})
        if request.method == "POST" and url.endswith("/pulls"):
            return httpx.Response(201, json={"number": 42, "html_url": "https://gh/pr/42"})
        return httpx.Response(404, json={})

    return handler


@pytest.mark.asyncio
async def test_dry_run_produces_pin_diff():
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls))) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        edits, resolved = await dry_run_repo(gh, "acme", "infra")
    assert len(edits) == 1
    assert ("actions", "checkout", "v4") in resolved
    edit = edits[0]
    assert edit.path == ".github/workflows/ci.yml"
    assert f"actions/checkout@{SHA}" in edit.new_text
    assert edit.diff.startswith("--- a/.github/workflows/ci.yml")
    assert "# v4" in edit.new_text
    # no writes happened during dry-run
    assert not any(m in ("POST", "PUT") for m, _ in calls)


@pytest.mark.asyncio
async def test_open_pr_flow():
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls))) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        edits, _ = await dry_run_repo(gh, "acme", "infra")
        pr = await open_pr_for_edits(
            gh,
            "acme",
            "infra",
            edits,
            base_branch="main",
            operation_id="pin-shas-1",
            rationale="Pin actions to SHAs.",
        )
    assert pr == {"number": 42, "html_url": "https://gh/pr/42"}
    methods = [m for m, _ in calls]
    assert "POST" in methods and "PUT" in methods  # branch + commit + PR happened
