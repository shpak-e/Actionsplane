"""Tests for the live event-bus envelope builder (pure) + subscription cleanup."""

from __future__ import annotations

import asyncio

import pytest

from actionsplane.events import build_envelope
from actionsplane.events.bus import CHANNEL, subscribe


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

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def aclose(self) -> None:
        self.closed = True

    async def listen(self):
        yield {"type": "subscribe"}
        yield {"type": "message", "data": b'{"kind":"run"}'}
        await asyncio.Event().wait()  # idle channel: block forever, like a live connection


class _FakeConn:
    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self) -> _FakePubSub:
        return self._pubsub

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_subscribe_cleans_up_when_consumer_closes():
    """Closing the generator (what a client disconnect does) must unsubscribe + close pubsub."""
    ps = _FakePubSub()
    conn = _FakeConn(ps)
    stream = subscribe(conn=conn)

    # the control "subscribe" frame is skipped; the first real message is relayed
    assert await stream.__anext__() == '{"kind":"run"}'
    await stream.aclose()  # simulate the SSE generator being closed on disconnect

    assert ps.unsubscribed == [CHANNEL]  # finally ran
    assert ps.closed is True
    assert conn.closed is False  # injected (not owned) connection is left for the caller to manage
