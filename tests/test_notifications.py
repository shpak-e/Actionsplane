"""Notifications: idempotency ledger, event fan-out/filtering, senders, and channel endpoints."""

from __future__ import annotations

import base64  # noqa: F401  (kept parallel with other tests' import style)
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import NotificationChannel
from actionsplane.db.repository import (
    create_notification_channel,
    list_notification_channels,
    mark_notified,
)
from actionsplane.notifications import service


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_mark_notified_is_idempotent(session):
    assert await mark_notified(session, "finding:abc") is True
    assert await mark_notified(session, "finding:abc") is False  # second time: already claimed
    assert await mark_notified(session, "finding:def") is True


async def test_dispatch_filters_by_subscription_and_enabled(session, monkeypatch):
    sent: list[tuple[int, str]] = []

    async def fake_deliver(channel, event_type, title, body, link, http):
        sent.append((channel.id, event_type))

    monkeypatch.setattr(service, "_deliver", fake_deliver)

    a = await create_notification_channel(
        session, kind="slack", name="a", config={"url": "x"}, events=["finding.high"]
    )
    b = await create_notification_channel(
        session, kind="webhook", name="b", config={"url": "y"}, events=["run.failed"]
    )
    c = await create_notification_channel(
        session, kind="slack", name="c", config={"url": "z"}, events=["finding.high"]
    )
    c.enabled = False  # disabled channels never fire
    await session.commit()

    n = await service.dispatch(session, "finding.high", title="t", body="b")
    assert n == 1
    assert sent == [(a.id, "finding.high")]  # only the enabled, subscribed channel
    assert b.id not in [s[0] for s in sent]


async def test_dispatch_once_sends_only_first_time(session, monkeypatch):
    calls: list[str] = []

    async def fake_deliver(channel, event_type, title, body, link, http):
        calls.append(event_type)

    monkeypatch.setattr(service, "_deliver", fake_deliver)
    await create_notification_channel(
        session, kind="slack", name="a", config={"url": "x"}, events=["campaign.pr"]
    )
    await session.commit()

    first = await service.dispatch_once(session, "campaign:1", "campaign.pr", title="t", body="b")
    second = await service.dispatch_once(session, "campaign:1", "campaign.pr", title="t", body="b")
    assert first == 1 and second == 0
    assert calls == ["campaign.pr"]


async def test_dispatch_is_noop_when_globally_disabled(session, monkeypatch):
    from actionsplane.config import get_settings

    monkeypatch.setattr(get_settings(), "notifications_enabled", False)
    await create_notification_channel(
        session, kind="slack", name="a", config={"url": "x"}, events=["finding.high"]
    )
    await session.commit()
    assert await service.dispatch(session, "finding.high", title="t", body="b") == 0


@pytest.mark.asyncio
async def test_send_slack_and_webhook_payloads():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen[str(req.url)] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await service._send_slack("https://hooks.slack.com/abc", "Title", "Body", http)
        await service._send_webhook(
            "https://example.com/hook", "run.failed", "Title", "Body", "https://pr", http
        )

    assert seen["https://hooks.slack.com/abc"] == {"text": "*Title*\nBody"}
    wh = seen["https://example.com/hook"]
    assert wh["event"] == "run.failed" and wh["url"] == "https://pr" and wh["source"] == "actionsplane"


async def test_create_channel_endpoint_rejects_unknown_event(session):
    from fastapi import HTTPException

    from actionsplane.api.app import create_channel_endpoint
    from actionsplane.api.schemas import NotificationChannelIn

    with pytest.raises(HTTPException) as exc:
        await create_channel_endpoint(
            NotificationChannelIn(kind="slack", name="x", config={"url": "u"}, events=["nope"]),
            session=session,
            actor="tester",
        )
    assert exc.value.status_code == 422


async def test_create_channel_endpoint_requires_destination(session):
    from fastapi import HTTPException

    from actionsplane.api.app import create_channel_endpoint
    from actionsplane.api.schemas import NotificationChannelIn

    with pytest.raises(HTTPException) as exc:
        await create_channel_endpoint(
            NotificationChannelIn(kind="email", name="x", config={}, events=[]),
            session=session,
            actor="tester",
        )
    assert exc.value.status_code == 422  # email needs config.to


async def test_list_channels_masks_destination(session):
    from actionsplane.api.app import list_channels_endpoint

    await create_notification_channel(
        session,
        kind="slack",
        name="ops",
        config={"url": "https://hooks.slack.com/services/T00/B00/SECRET123"},
        events=["finding.high"],
    )
    await session.commit()
    out = await list_channels_endpoint(session=session, actor="tester")
    assert len(out) == 1
    assert "SECRET123" not in (out[0].target_hint or "")  # secret is masked
    assert out[0].kind == "slack" and out[0].events == ["finding.high"]


def test_mask_destination_helper():
    from actionsplane.api.app import _mask_destination

    masked = _mask_destination("slack", {"url": "https://h.slack.com/services/T0/B0/SECRETtoken99"})
    assert "SECRETtoken99" not in masked and "h.slack.com" in masked
    assert _mask_destination("email", {"to": "alice@example.com"}) == "a***@example.com"


@pytest.mark.asyncio
async def test_deliver_email_without_smtp_raises(monkeypatch):
    ch = NotificationChannel(id=1, kind="email", name="e", config={"to": "a@b.com"}, events=[])
    async with httpx.AsyncClient() as http:
        with pytest.raises(Exception, match="SMTP"):
            await service._deliver(ch, "test", "t", "b", None, http)
