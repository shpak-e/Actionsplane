"""Thin arq enqueue helper (plan §4, Phase 1).

The ingestor stays hot-path-thin: it verifies, persists nothing heavy, and enqueues the raw
event for the worker. Connection pooling is handled by arq; we open a pool lazily and reuse it.
"""

from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from actionsplane.config import get_settings
from actionsplane.observability import inject_context

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def enqueue_event(event: str, payload: dict) -> None:
    """Enqueue a webhook event for the sync worker to process.

    The current trace context rides along as ``_trace`` so the worker's processing span chains to
    this ingest span — one end-to-end trace across the queue (no-op carrier when tracing is off).
    """
    pool = await get_pool()
    await pool.enqueue_job("process_event", event, payload, _trace=inject_context())
