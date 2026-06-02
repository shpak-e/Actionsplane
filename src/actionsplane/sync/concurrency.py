"""Bounded concurrency helper for the org-wide sweeps (plan §10 / review).

The audit/drift/reconcile sweeps fan out over every watched repo. Running them serially stalls on
large orgs and wastes wall-clock; running them fully unbounded would hammer GitHub's rate limit
and exhaust the DB connection pool. ``bounded_gather`` caps in-flight work at ``limit`` while
still returning results in input order.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from typing import TypeVar

T = TypeVar("T")


async def bounded_gather(aws: Sequence[Awaitable[T]], *, limit: int) -> list[T]:  # noqa: UP047
    """Like ``asyncio.gather`` but with at most ``limit`` coroutines running at once."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    sem = asyncio.Semaphore(limit)

    async def _run(aw: Awaitable[T]) -> T:
        async with sem:
            return await aw

    return list(await asyncio.gather(*(_run(a) for a in aws)))
