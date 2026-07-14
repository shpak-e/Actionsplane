"""Tests for the event-bus envelope builder (pure) + the fan-out hub (review §5 M6 / §4 L-5)."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from actionsplane.events import build_envelope
from actionsplane.events.bus import CHANNEL, EventHub, SubscriberLimit


def test_build_envelope_slims_payload():
    full = {
        "id": 1,
        "repo_id": 2,
        "workflow_id": 3,
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "raw_payload": {"huge": "blob"},
        "actor": "x",
    }
    env = build_envelope("run", full)
    assert env["kind"] == "run"
    assert env["data"] == {
        "id": 1,
        "repo_id": 2,
        "workflow_id": 3,
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
    }
    # the heavy/irrelevant fields are dropped from the live tick
    assert "raw_payload" not in env["data"]
    assert "actor" not in env["data"]


def test_build_envelope_job_with_run_id():
    env = build_envelope("job", {"id": 9, "run_id": 5001, "conclusion": "failure"})
    assert env == {"kind": "job", "data": {"id": 9, "run_id": 5001, "conclusion": "failure"}}


class _FakePubSub:
    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False
        self._queue: asyncio.Queue = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def aclose(self) -> None:
        self.closed = True

    def emit(self, data: bytes) -> None:
        self._queue.put_nowait({"type": "message", "data": data})

    async def listen(self):
        yield {"type": "subscribe"}
        while True:
            yield await self._queue.get()


class _FakeConn:
    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self) -> _FakePubSub:
        return self._pubsub

    async def aclose(self) -> None:
        self.closed = True


async def _next(stream):
    return await asyncio.wait_for(stream.__anext__(), timeout=1.0)


@pytest.mark.asyncio
async def test_hub_fans_one_message_out_to_all_subscribers():
    """M6: one Redis reader, many clients — a single publish reaches every subscriber's queue."""
    ps = _FakePubSub()
    conn = _FakeConn(ps)
    hub = EventHub(conn_factory=lambda: conn)

    a = hub.subscribe()
    b = hub.subscribe()
    # prime both generators so their queues are registered before we emit
    task_a = asyncio.ensure_future(_next(a))
    task_b = asyncio.ensure_future(_next(b))
    await asyncio.sleep(0)  # let both register + the reader start
    ps.emit(b'{"kind":"run"}')

    assert await task_a == '{"kind":"run"}'
    assert await task_b == '{"kind":"run"}'
    assert ps.subscribed == [CHANNEL]  # ONE subscription shared across both clients

    await a.aclose()
    assert conn.closed is False  # one client left → reader still running
    await b.aclose()
    assert ps.unsubscribed == [CHANNEL] and conn.closed is True  # last client → reader torn down


@pytest.mark.asyncio
async def test_hub_enforces_subscriber_cap():
    """L-5: past the cap, a new subscriber is refused rather than allocating another queue."""
    ps = _FakePubSub()
    hub = EventHub(conn_factory=lambda: _FakeConn(ps), max_subscribers=1)

    first = hub.subscribe()
    task = asyncio.ensure_future(_next(first))
    await asyncio.sleep(0)  # register the first subscriber

    with pytest.raises(SubscriberLimit):
        await hub.subscribe().__anext__()

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task  # let the cancel settle before closing the generator
    await first.aclose()
