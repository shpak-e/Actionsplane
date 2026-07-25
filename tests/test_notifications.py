"""Notifications: idempotency ledger, event fan-out/filtering, senders, and channel endpoints."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import Base
from actionsplane.db.models import NotificationChannel
from actionsplane.db.repository import create_notification_channel, mark_notified
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
async def test_send_slack_and_webhook_payloads(monkeypatch):
    seen: dict = {}

    async def fake_resolve(host: str) -> str:
        return "93.184.216.34"  # skip real DNS; pinning connects here

    monkeypatch.setattr(service, "_resolve_public_ip", fake_resolve)

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        # The connection is pinned to the IP, but the Host header stays on the real hostname.
        assert req.url.host == "93.184.216.34"
        seen[req.headers["Host"]] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await service._send_slack("https://hooks.slack.com/abc", "Title", "Body", http)
        await service._send_webhook(
            "https://example.com/hook", "run.failed", "Title", "Body", "https://pr", http
        )

    assert seen["hooks.slack.com"] == {"text": "*Title*\nBody"}
    wh = seen["example.com"]
    assert wh["event"] == "run.failed" and wh["url"] == "https://pr"
    assert wh["source"] == "actionsplane"


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


# --- SSRF / destination validation ---------------------------------------------------------------


def test_validate_destination_format():
    service.validate_destination("slack", {"url": "https://hooks.slack.com/x"})  # ok
    service.validate_destination("webhook", {"url": "http://example.com/h"})  # http allowed
    service.validate_destination("email", {"to": "a@b.com"})  # ok
    import pytest as _pytest

    with _pytest.raises(ValueError, match="https"):
        service.validate_destination("slack", {"url": "http://hooks.slack.com/x"})  # needs TLS
    with _pytest.raises(ValueError):
        service.validate_destination("webhook", {"url": "ftp://example.com"})  # bad scheme
    with _pytest.raises(ValueError):
        service.validate_destination("webhook", {"url": "https://"})  # no host
    with _pytest.raises(ValueError, match="recipient"):
        service.validate_destination("email", {"to": "a@b.com\r\nBcc: x@y.com"})  # CRLF injection
    with _pytest.raises(ValueError, match="recipient"):
        service.validate_destination("email", {"to": "not-an-email"})


@pytest.mark.asyncio
async def test_resolve_public_ip_blocks_internal_targets():
    # literal IPs → getaddrinfo returns them verbatim, no real DNS lookup
    for bad in (
        "127.0.0.1",  # loopback
        "169.254.169.254",  # cloud metadata / link-local
        "10.0.0.5",  # private
        "192.168.1.1",  # private
    ):
        with pytest.raises(ValueError, match=r"non-public|resolve"):
            await service._resolve_public_ip(bad)
    # a public literal IP passes and is returned for pinning
    assert await service._resolve_public_ip("8.8.8.8") == "8.8.8.8"


@pytest.mark.asyncio
async def test_create_channel_endpoint_blocks_ssrf(session):
    from fastapi import HTTPException

    from actionsplane.api.app import create_channel_endpoint
    from actionsplane.api.schemas import NotificationChannelIn

    with pytest.raises(HTTPException) as exc:
        await create_channel_endpoint(
            NotificationChannelIn(
                kind="webhook",
                name="evil",
                config={"url": "http://169.254.169.254/latest/meta-data/"},
                events=[],
            ),
            session=session,
            actor="tester",
        )
    assert exc.value.status_code == 422


def test_safe_error_never_leaks_url():
    req = httpx.Request("POST", "https://hooks.slack.com/services/T0/B0/SECRETtoken")
    resp = httpx.Response(500, request=req)
    http_err = httpx.HTTPStatusError("boom", request=req, response=resp)
    assert service.safe_error(http_err) == "destination returned HTTP 500"
    # a connect error's str embeds the URL/host — safe_error must reduce it to the type
    conn_err = httpx.ConnectError("failed to connect to https://hooks.slack.com/SECRET")
    out = service.safe_error(conn_err)
    assert "SECRET" not in out and "ConnectError" in out
