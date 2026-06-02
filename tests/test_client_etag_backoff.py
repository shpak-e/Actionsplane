"""ETag/304 caching and Retry-After backoff in the GitHub client."""

from __future__ import annotations

import httpx
import pytest

from actionsplane.github.client import GitHubClient


@pytest.mark.asyncio
async def test_etag_304_serves_cached_body():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == '"abc"':
            return httpx.Response(304)
        return httpx.Response(
            200,
            json={"total_count": 1, "workflow_runs": [{"id": 1}]},
            headers={"etag": '"abc"'},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        first = await gh.list_workflow_runs("acme", "infra")
        second = await gh.list_workflow_runs("acme", "infra")
    assert first == second == [{"id": 1}]
    # first call had no If-None-Match; second sent the etag we stored
    assert calls == [None, '"abc"']


@pytest.mark.asyncio
async def test_retry_after_backoff_then_succeeds(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("actionsplane.github.client.asyncio.sleep", fake_sleep)

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"message": "slow down"})
        return httpx.Response(
            200,
            json={"workflow_runs": []},
            headers={"etag": '"e"'},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        result = await gh.list_workflow_runs("acme", "infra")
    assert result == []
    assert attempts["n"] == 2  # one retry
    assert sleeps == [1.0]  # honored Retry-After


@pytest.mark.asyncio
async def test_secondary_rate_limit_403_with_retry_after():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(403, headers={"retry-after": "0"}, json={"message": "secondary"})
        return httpx.Response(200, json={"workflow_runs": []}, headers={"etag": '"e"'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        await gh.list_workflow_runs("acme", "infra")
    assert attempts["n"] == 2
