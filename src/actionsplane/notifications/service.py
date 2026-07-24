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
import ipaddress
import logging
import re
import smtplib
import socket
from email.message import EmailMessage
from email.policy import default as default_email_policy
from urllib.parse import urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.config import get_settings
from actionsplane.db.models import NotificationChannel
from actionsplane.db.repository import list_notification_channels, mark_notified

log = logging.getLogger(__name__)

# Channel-destination validation (SSRF guard). Operator-supplied URLs are POSTed by the server, so a
# URL must be http(s) (slack: https only) and must NOT resolve to a private / loopback / linklocal /
# reserved address — otherwise an operator could pivot the server into the internal network or the
# cloud metadata endpoint (169.254.169.254). Redirects are disabled at the client so a 30x can't
# bypass this. Recipients are validated to a single address with no CR/LF (SMTP header injection).
_ALLOWED_SCHEMES: dict[str, set[str]] = {"slack": {"https"}, "webhook": {"http", "https"}}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_destination(kind: str, config: dict) -> None:
    """Sync format check for a channel destination. Raises ``ValueError`` on anything malformed.
    (The IP-range SSRF check is done at send time — see ``_assert_public_url`` — to also cover the
    resolve-just-before-connect window.)"""
    cfg = config or {}
    if kind == "email":
        to = str(cfg.get("to", "")).strip()
        if not to or "\r" in to or "\n" in to or not _EMAIL_RE.match(to):
            raise ValueError("invalid email recipient")
        return
    url = str(cfg.get("url", "")).strip()
    parts = urlsplit(url)
    allowed = _ALLOWED_SCHEMES.get(kind, {"https"})
    if parts.scheme not in allowed:
        raise ValueError(f"{kind} URL must use {' or '.join(sorted(allowed))}")
    host = parts.hostname
    if not host:
        raise ValueError("URL has no host")
    # If the host is a literal IP, reject non-public ones now (no DNS needed — works even where the
    # API can't resolve arbitrary hosts). Hostnames are DNS-checked at send time (see _deliver).
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not _is_public(str(ip)):
        raise ValueError("destination address is non-public (blocked)")


def _is_public(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _assert_public_url(url: str) -> None:
    """Resolve the host; reject if any address is non-public (SSRF). Raises ``ValueError``."""
    host = urlsplit(url).hostname
    if not host:
        raise ValueError("URL has no host")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve destination host {host!r}") from exc
    if not all(_is_public(info[4][0]) for info in infos):
        raise ValueError("destination resolves to a non-public address (blocked)")


def safe_error(exc: Exception) -> str:
    """A delivery-failure message safe to log / return — never echoes the channel's secret URL.
    Our own validation/config errors are kept (they carry no secret); network errors are reduced to
    their type + any HTTP status, since httpx error strings embed the full URL."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"destination returned HTTP {exc.response.status_code}"
    if isinstance(exc, ValueError | RuntimeError):
        return str(exc)[:200]
    return f"delivery failed ({type(exc).__name__})"


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
    if "\r" in to or "\n" in to:  # defense-in-depth vs SMTP header injection
        raise ValueError("invalid email recipient")
    msg = EmailMessage(policy=default_email_policy)
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
        url = str(cfg.get("url", ""))
        await _assert_public_url(url)
        await _send_slack(url, title, body, http)
    elif channel.kind == "webhook":
        url = str(cfg.get("url", ""))
        await _assert_public_url(url)
        await _send_webhook(url, event_type, title, body, link, http)
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
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as http:
        for channel in channels:
            try:
                await _deliver(channel, event_type, title, body, link, http)
                delivered += 1
            except Exception as exc:  # one bad channel must never break the caller
                log.warning(
                    "notification to channel %s (%s) failed: %s",
                    channel.id,
                    channel.kind,
                    safe_error(exc),
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
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as http:
        await _deliver(
            channel,
            "test",
            "Test alert",
            f"This is a test alert from ActionsPlane to “{channel.name}”. If you can read this, "
            "the channel is wired up correctly.",
            None,
            http,
        )
