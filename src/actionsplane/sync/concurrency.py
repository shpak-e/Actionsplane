"""Bounded concurrency helper for the org-wide sweeps (plan §10 / review).

The audit/drift/reconcile sweeps fan out over every watched repo. Running them serially stalls on
large orgs and wastes wall-clock; running them fully unbounded would hammer GitHub's rate limit
and exhaust the DB connection pool. ``bounded_gather`` caps in-flight work at ``limit`` while
isolating failures: one repo raising (a transient GitHub error, a closed transport) must never
abort the whole fleet sweep (review 4, NEW-1).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


async def bounded_gather(aws: Sequence[Awaitable[T]], *, limit: int) -> list[T]:  # noqa: UP047
    """Run ``aws`` with at most ``limit`` running at once; return the successful results.

    Unlike a bare ``asyncio.gather``, one awaitable raising does not cancel its siblings: the
    exception is logged and that task is dropped from the result, so a single bad repo can't abort
    a fleet sweep (review 4, NEW-1). Cancellation (and other ``BaseException``) still propagates so
    an arq ``job_timeout`` can stop the sweep. Order is not guaranteed to track the input.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    sem = asyncio.Semaphore(limit)

    async def _run(aw: Awaitable[T]) -> T:
        async with sem:
            return await aw

    results = await asyncio.gather(*(_run(a) for a in aws), return_exceptions=True)
    out: list[T] = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("sweep task failed, skipping it (remaining tasks unaffected): %r", r)
        elif isinstance(r, BaseException):
            raise r  # CancelledError / KeyboardInterrupt / SystemExit — don't swallow
        else:
            out.append(r)
    return out
