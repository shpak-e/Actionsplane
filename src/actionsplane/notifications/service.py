"""Alert routing: fan a fleet event out to the operator's Slack / webhook / email channels.

Channels + their event subscriptions are stored in the DB (``notification_channels``); this module
formats an event and delivers it to every enabled channel subscribed to that event type. Delivery
is **fail-safe** on the hot paths — a broken channel logs and is skipped, never breaking an audit,
campaign apply, or webhook ingest. ``dispatch_once`` de-dupes via the ``notification_sent`` ledger
so a finding re-seen every sweep (or a still-urgent deprecation) alerts exactly once. ``send_test``
is the one path that surfaces errors (so the UI can show a failed test).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.config import get_settings
from actionsplane.db.models import NotificationChannel
from actionsplane.db.repository import list_notification_channels, mark_notified

log = logging.getLogger(__name__)

# The event types a channel may subscribe to, with UI-facing labels/descriptions.
EVENT_TYPES: dict[str, dict[str, str]] = {
    "finding.high": {
        "label": "New critical/high finding",
        "description": "A new security finding at critical or high severity is detected.",
    },
    "campaign.pr": {
        "label": "Campaign PR opened / failed",
        "description": "A fix campaign opens a pull request, or a target errors out.",
    },
    "radar.deadline": {
        "label": "Deprecation deadline near",
        "description": "A runner/action deprecation is overdue or due within 90 days.",
    },
    "run.failed": {
        "label": "Workflow run failed",
        "description": "A watched workflow run concludes as a failure.",
    },
}

KINDS = ("slack", "webhook", "email")


async def _send_slack(url: str, title: str, body: str, http: httpx.AsyncClient) -> None:
    resp = await http.post(url, json={"text": f"*{title}*\n{body}"})
    resp.raise_for_status()


async def _send_webhook(
    url: str, event_type: str, title: str, body: str, link: str | None, http: httpx.AsyncClient
) -> None:
    payload = {"source": "actionsplane", "event": event_type, "title": title, "body": body}
    if link:
        payload["url"] = link
    resp = await http.post(url, json=payload)
    resp.raise_for_status()


def _smtp_send(to: str, subject: str, body: str) -> None:
    s = get_settings()
    if not s.smtp_host:
        raise RuntimeError("email channel requires ACTIONSPLANE_SMTP_HOST to be configured")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s.smtp_from or s.smtp_username or "actionsplane@localhost"
    msg["To"] = to
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as server:
        if s.smtp_use_tls:
            server.starttls()
        if s.smtp_username:
            server.login(s.smtp_username, s.smtp_password or "")
        server.send_message(msg)


async def _deliver(
    channel: NotificationChannel,
    event_type: str,
    title: str,
    body: str,
    link: str | None,
    http: httpx.AsyncClient,
) -> None:
    """Send one message to one channel. Raises on failure (caller decides whether to swallow)."""
    cfg = channel.config or {}
    if channel.kind == "slack":
        await _send_slack(str(cfg.get("url", "")), title, body, http)
    elif channel.kind == "webhook":
        await _send_webhook(str(cfg.get("url", "")), event_type, title, body, link, http)
    elif channel.kind == "email":
        subject = f"[ActionsPlane] {title}"
        text = f"{body}\n\n{link}" if link else body
        await asyncio.to_thread(_smtp_send, str(cfg.get("to", "")), subject, text)
    else:  # pragma: no cover - guarded at creation
        raise ValueError(f"unknown channel kind {channel.kind!r}")


async def dispatch(
    session: AsyncSession, event_type: str, *, title: str, body: str, link: str | None = None
) -> int:
    """Deliver an event to every enabled channel subscribed to it. Fail-safe: a broken channel is
    logged and skipped. Returns the number of channels delivered to."""
    settings = get_settings()
    if not settings.notifications_enabled:
        return 0
    channels = [
        c
        for c in await list_notification_channels(session, enabled_only=True)
        if event_type in (c.events or [])
    ]
    if not channels:
        return 0
    delivered = 0
    async with httpx.AsyncClient(timeout=15) as http:
        for channel in channels:
            try:
                await _deliver(channel, event_type, title, body, link, http)
                delivered += 1
            except Exception as exc:  # one bad channel must never break the caller
                log.warning(
                    "notification to channel %s (%s) failed: %s", channel.id, channel.kind, exc
                )
    return delivered


async def dispatch_once(
    session: AsyncSession,
    event_key: str,
    event_type: str,
    *,
    title: str,
    body: str,
    link: str | None = None,
) -> int:
    """Like :func:`dispatch`, but only the first call for a given ``event_key`` sends (idempotency
    ledger). Commits the claim so a crash mid-send doesn't double-fire on retry."""
    if not await mark_notified(session, event_key):
        return 0
    await session.commit()
    return await dispatch(session, event_type, title=title, body=body, link=link)


async def send_test(channel: NotificationChannel) -> None:
    """Deliver a test message to one channel. Raises on failure so the UI can report it."""
    async with httpx.AsyncClient(timeout=15) as http:
        await _deliver(
            channel,
            "test",
            "Test alert",
            f"This is a test alert from ActionsPlane to “{channel.name}”. If you can read this, "
            "the channel is wired up correctly.",
            None,
            http,
        )
