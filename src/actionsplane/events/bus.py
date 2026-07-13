"""Live event bus over Redis pub/sub (plan §5.1 — sub-second UI updates via SSE).

The worker publishes a small envelope after it persists each run/job. The API's SSE endpoint
subscribes and relays envelopes to connected browsers. Redis pub/sub (rather than a queue) is the
right primitive: fan-out to N dashboards, no durability needed — a missed live tick is harmless
because the REST read model is the source of truth.

Fan-out (review §5 M6): a single process-wide reader holds ONE Redis pubsub connection and pushes
each envelope into a per-client ``asyncio.Queue``, instead of every browser opening its own Redis
connection + decode loop. Subscribers are capped (§4 L-5) so an unauthenticated flood can't
exhaust memory. The reader starts when the first client connects and stops when the last leaves.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import redis.asyncio as aioredis

from actionsplane.config import get_settings

log = logging.getLogger(__name__)

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


class SubscriberLimit(RuntimeError):
    """Raised when the per-process SSE subscriber cap is reached (review §4 L-5)."""


class EventHub:
    """One Redis pubsub reader fanning out to many per-client queues (review §5 M6).

    ``conn_factory`` is injectable so the fan-out is unit-testable without a live Redis. The reader
    task is lazily started on the first subscriber and cancelled when the last one leaves, so an
    idle API process holds no Redis connection.
    """

    def __init__(
        self,
        *,
        conn_factory: Callable[[], aioredis.Redis] | None = None,
        max_subscribers: int | None = None,
        queue_size: int = 100,
    ) -> None:
        self._conn_factory = conn_factory or (
            lambda: aioredis.from_url(get_settings().effective_redis_url)
        )
        self._max = max_subscribers
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._reader: asyncio.Task | None = None
        self._conn: aioredis.Redis | None = None
        self._pubsub: Any = None
        self._lock = asyncio.Lock()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def _start(self) -> None:
        self._conn = self._conn_factory()
        self._pubsub = self._conn.pubsub()
        await self._pubsub.subscribe(CHANNEL)
        self._reader = asyncio.create_task(self._read_loop())

    async def _stop(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._pubsub is not None:
            await self._pubsub.unsubscribe(CHANNEL)
            await self._pubsub.aclose()
        if self._conn is not None:
            await self._conn.aclose()
        self._reader = self._pubsub = self._conn = None

    async def _read_loop(self) -> None:
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message["data"]
                envelope = data.decode() if isinstance(data, bytes) else data
                for q in list(self._subscribers):
                    try:
                        q.put_nowait(envelope)
                    except asyncio.QueueFull:
                        # A slow client drops live ticks; the REST read model still corrects it.
                        log.debug("SSE subscriber queue full — dropping a live tick")
        except asyncio.CancelledError:
            raise
        except Exception:  # a Redis hiccup shouldn't kill the process; the next subscriber restarts
            log.warning("SSE reader loop ended unexpectedly", exc_info=True)

    async def subscribe(self) -> AsyncIterator[str]:
        """Yield envelope strings for one client; registers a queue and cleans up on close."""
        cap = self._max if self._max is not None else get_settings().sse_max_subscribers
        if len(self._subscribers) >= cap:
            raise SubscriberLimit(f"SSE subscriber cap ({cap}) reached")
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            was_empty = not self._subscribers
            self._subscribers.add(queue)
            if was_empty:
                await self._start()
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._subscribers.discard(queue)
                if not self._subscribers:
                    await self._stop()


# Process-wide hub used by the API's SSE endpoint.
_hub = EventHub()


def subscribe() -> AsyncIterator[str]:
    """Yield JSON envelope strings as they arrive (for the SSE endpoint), via the shared hub."""
    return _hub.subscribe()
