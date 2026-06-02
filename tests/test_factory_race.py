"""Token mint must happen exactly once even when many coroutines hit a cold cache."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from actionsplane.github.factory import client_for_installation


@pytest.mark.asyncio
async def test_no_thundering_herd_on_cold_cache():
    mint_count = 0
    mint_started = asyncio.Event()

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        nonlocal mint_count
        mint_count += 1
        mint_started.set()
        # hold long enough that other coroutines pile up at the lock
        await asyncio.sleep(0.05)
        exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return httpx.Response(201, json={"token": "ghs_only_one", "expires_at": exp})

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_handler)) as http:
        cache: dict = {}
        # 10 concurrent requesters for the SAME installation
        results = await asyncio.gather(
            *[client_for_installation(42, http=http, jwt="j", token_cache=cache) for _ in range(10)]
        )
    assert mint_count == 1  # the whole point: lock collapsed the herd
    assert all(r._token == "ghs_only_one" for r in results)
    assert cache[42].token == "ghs_only_one"
