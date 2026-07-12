"""Tests for the installation-token cache (expiry-aware)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from actionsplane.github.factory import client_for_installation


def _handler(counter):
    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(1)
        # token expires an hour out
        exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return httpx.Response(201, json={"token": f"ghs_{len(counter)}", "expires_at": exp})

    return handler


@pytest.mark.asyncio
async def test_token_cached_until_expiry():
    calls: list[int] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls))) as http:
        cache: dict = {}
        await client_for_installation(1, http=http, jwt="j", token_cache=cache)
        await client_for_installation(1, http=http, jwt="j", token_cache=cache)
        await client_for_installation(1, http=http, jwt="j", token_cache=cache)
    assert len(calls) == 1  # token minted once, reused while valid


@pytest.mark.asyncio
async def test_expired_token_is_refreshed():
    from actionsplane.github.app_auth import InstallationToken

    calls: list[int] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls))) as http:
        past = datetime.now(UTC) - timedelta(hours=2)
        cache = {1: InstallationToken("stale", past)}  # already expired
        await client_for_installation(1, http=http, jwt="j", token_cache=cache)
    assert len(calls) == 1  # stale token forced a refresh
    assert cache[1].token == "ghs_1"


@pytest.mark.asyncio
async def test_shared_etag_cache_per_installation():
    """Each call gets a fresh GitHubClient (so concurrent sweeps never share a closeable transport,
    review 4 NEW-1) but same-installation clients share one ETag/rate-budget cache so the
    conditional-request cache survives across sweeps (review 3, 4c); distinct installations get
    distinct caches."""
    from actionsplane.github import factory

    factory._caches.clear()  # isolate from other tests' module-global cache
    calls: list[int] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls))) as http:
        cache: dict = {}
        c1 = await client_for_installation(1, http=http, jwt="j", token_cache=cache)
        c2 = await client_for_installation(1, http=http, jwt="j", token_cache=cache)
        other = await client_for_installation(2, http=http, jwt="j", token_cache=cache)
    assert c1 is not c2  # fresh object per call — nothing closeable shared across sweeps
    assert c1._cache is c2._cache  # ...but the ETag cache + rate budget persist per installation
    assert other._cache is not c1._cache


@pytest.mark.asyncio
async def test_overlapping_sweeps_do_not_share_a_closeable_client():
    """NEW-1 regression: a webhook-driven task and a reconcile sweep run concurrently against the
    same installation. Each opens its own httpx client; when one finishes and closes its transport,
    the other must still be able to issue requests. Pre-fix they shared one rebindable client and
    the closing task killed the other with ``client has been closed``."""
    from actionsplane.github import factory

    factory._caches.clear()

    def token_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return httpx.Response(201, json={"token": "ghs_x", "expires_at": exp})
        return httpx.Response(200, json={"id": 1, "default_branch": "main"})

    cache: dict = {}
    # Sweep A and Sweep B each own their own transport (as the arq worker jobs do).
    async with httpx.AsyncClient(transport=httpx.MockTransport(token_handler)) as http_b:
        async with httpx.AsyncClient(transport=httpx.MockTransport(token_handler)) as http_a:
            gh_a = await client_for_installation(7, http=http_a, jwt="j", token_cache=cache)
            gh_b = await client_for_installation(7, http=http_b, jwt="j", token_cache=cache)
            await gh_a.get_repo_meta("acme", "infra")  # both live
        # http_a is now closed (Sweep A finished); Sweep B must still work.
        assert gh_b._cache is gh_a._cache  # they did share the ETag cache...
        await gh_b.get_repo_meta("acme", "infra")  # ...but NOT the transport — no RuntimeError
