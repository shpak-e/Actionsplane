"""bounded_gather: caps concurrency and isolates failures so one bad repo can't abort a sweep
(review 4, NEW-1)."""

from __future__ import annotations

import asyncio

import pytest

from actionsplane.sync.concurrency import bounded_gather


@pytest.mark.asyncio
async def test_one_raising_task_does_not_cancel_the_rest():
    async def ok(n: int) -> int:
        return n

    async def boom() -> int:
        raise RuntimeError("Cannot send a request, as the client has been closed.")

    results = await bounded_gather([ok(1), boom(), ok(2), boom(), ok(3)], limit=2)
    assert sorted(results) == [1, 2, 3]  # failures logged + dropped, survivors returned


@pytest.mark.asyncio
async def test_respects_the_concurrency_limit():
    running = 0
    peak = 0

    async def task() -> int:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return 1

    results = await bounded_gather([task() for _ in range(10)], limit=3)
    assert len(results) == 10
    assert peak <= 3  # never more than `limit` in flight


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed():
    async def cancel_me() -> int:
        raise asyncio.CancelledError

    async def ok() -> int:
        return 1

    with pytest.raises(asyncio.CancelledError):
        await bounded_gather([ok(), cancel_me()], limit=2)


@pytest.mark.asyncio
async def test_limit_must_be_positive():
    with pytest.raises(ValueError):
        await bounded_gather([], limit=0)
