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
async def test_same_client_instance_reused_per_installation():
    """One GitHubClient per installation, reused across calls/sweeps so its ETag cache survives
    (review 3, 4c); distinct installations get distinct clients."""
    from actionsplane.github import factory

    factory._clients.clear()  # isolate from other tests' module-global cache
    calls: list[int] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls))) as http:
        cache: dict = {}
        c1 = await client_for_installation(1, http=http, jwt="j", token_cache=cache)
        c2 = await client_for_installation(1, http=http, jwt="j", token_cache=cache)
        other = await client_for_installation(2, http=http, jwt="j", token_cache=cache)
    assert c1 is c2  # same instance → conditional-request cache persists
    assert other is not c1
