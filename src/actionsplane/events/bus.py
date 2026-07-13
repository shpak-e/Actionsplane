"""Live event bus over Redis pub/sub (plan §5.1 — sub-second UI updates via SSE).

The worker publishes a small envelope after it persists each run/job. The API's SSE endpoint
subscribes to the same Redis channel and relays envelopes to connected browsers. Redis pub/sub
(rather than a queue) is the right primitive here: fan-out to N dashboards, no durability needed
— a missed live tick is harmless because the REST read model is the source of truth.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from actionsplane.config import get_settings

CHANNEL = "actionsplane:events"

_publisher: aioredis.Redis | None = None


async def _publisher_conn() -> aioredis.Redis:
    """Process-wide Redis connection for publishing (opened once, reused)."""
    global _publisher
    if _publisher is None:
        _publisher = aioredis.from_url(get_settings().effective_redis_url)
    return _publisher


def build_envelope(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build the minimal live-update envelope sent to the UI.

    ``kind`` is e.g. "run" or "job"; payload carries just enough for the UI to update a row
    (id, repo_id, status, conclusion) without a full refetch.
    """
    keys = ("id", "repo_id", "run_id", "workflow_id", "status", "conclusion", "head_branch")
    slim = {k: payload[k] for k in keys if k in payload}
    return {"kind": kind, "data": slim}


async def publish(
    kind: str, payload: dict[str, Any], *, redis: aioredis.Redis | None = None
) -> None:
    """Publish a live update, reusing a process-wide connection (override with ``redis``)."""
    conn = redis or await _publisher_conn()
    await conn.publish(CHANNEL, json.dumps(build_envelope(kind, payload)))


async def subscribe(*, conn: aioredis.Redis | None = None) -> AsyncIterator[str]:
    """Yield JSON envelope strings as they arrive on the channel (for the SSE endpoint).

    Cleanup is guaranteed in the ``finally``: when the consumer stops — a browser tab closes,
    so the API's SSE generator is ``aclose()``'d and a ``GeneratorExit`` propagates here — we
    unsubscribe and close the pubsub (and the connection, if we opened it). Without this, every
    dropped client would strand a Redis connection parked in ``listen()``. ``conn`` is injectable
    so the cleanup path is unit-testable without a live Redis.
    """
    owns_conn = conn is None
    conn = conn if conn is not None else aioredis.from_url(get_settings().effective_redis_url)
    pubsub = conn.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                data = message["data"]
                yield data.decode() if isinstance(data, bytes) else data
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()
        if owns_conn:
            await conn.aclose()
