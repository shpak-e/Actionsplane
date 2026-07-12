"""ETag/304 caching and Retry-After backoff in the GitHub client."""

from __future__ import annotations

import math

import httpx
import pytest

from actionsplane.github.client import GitHubClient, _retry_after_delay


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30", 30.0),
        ("120", 60.0),  # clamped to the 60s ceiling
        ("0", 0.0),
        ("-5", 0.0),  # negatives floored to 0
        ("abc", 0.0),  # non-numeric → no crash, retry immediately
        ("nan", 0.0),  # min(nan, 60) is nan → would park forever; must become 0
        ("inf", 0.0),
        ("-inf", 0.0),
        ("1e309", 0.0),  # overflows to inf
    ],
)
def test_retry_after_delay_is_finite_and_bounded(raw, expected):
    delay = _retry_after_delay(raw)
    assert math.isfinite(delay)
    assert 0.0 <= delay <= 60.0
    assert delay == expected


@pytest.mark.parametrize("bad", ["abc", "nan", "inf", "-inf", "1e309"])
@pytest.mark.asyncio
async def test_garbled_retry_after_survives_a_real_request(monkeypatch, bad):
    """A hostile/garbled Retry-After (NaN, non-numeric, overflow) must neither crash the sweep nor
    park the task forever — the request survives and the sleep is finite (review 4, NEW-2)."""
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("actionsplane.github.client.asyncio.sleep", fake_sleep)

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"retry-after": bad}, json={"message": "slow down"})
        return httpx.Response(200, json={"workflow_runs": []}, headers={"etag": '"e"'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        result = await gh.list_workflow_runs("acme", "infra")
    assert result == []
    assert attempts["n"] == 2  # retried once, did not crash
    assert len(sleeps) == 1
    assert math.isfinite(sleeps[0]) and 0.0 <= sleeps[0] <= 60.0
