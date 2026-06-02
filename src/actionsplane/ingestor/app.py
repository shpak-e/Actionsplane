"""Webhook ingestor (plan §4, Phase 1).

A deliberately thin FastAPI app: enforce a body-size cap, verify the HMAC, dedup the delivery
via ``X-GitHub-Delivery`` (so at-least-once delivery becomes effectively-once for side effects),
and hand the raw payload to the async worker. Persistence still happens in the worker — the
ingestor only writes one row (the dedup record), keeping its 10s budget comfortable.

Hardening from review-2: ``X-GitHub-Delivery`` dedup, body-size cap, ``json.loads`` guarded.
"""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.config import get_settings
from actionsplane.db.base import get_session
from actionsplane.db.repository import try_record_delivery
from actionsplane.ingestor.signature import verify_signature
from actionsplane.observability import instrument_fastapi, setup_tracing
from actionsplane.sync.queue import enqueue_event

setup_tracing("actionsplane-ingestor")
app = FastAPI(title="ActionsPlane Ingestor", version="0.0.0")
instrument_fastapi(app)

# GitHub doc'd max is ~25MB; we cap well below that to bound memory per pod.
MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB

_HANDLED_EVENTS = {
    "workflow_run",
    "workflow_job",
    "push",
    "installation",
    "installation_repositories",
}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_github_delivery: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    settings = get_settings()

    # Body-size cap before reading: trust Content-Length, then verify after read.
    cl = request.headers.get("content-length")
    if cl is not None and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="webhook body too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="webhook body too large")

    if not settings.github_webhook_secret:
        # Fail closed: never accept unsigned events in any environment.
        raise HTTPException(status_code=503, detail="webhook secret not configured")
    if not verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    if x_github_event == "ping":
        return {"status": "pong"}
    if x_github_event not in _HANDLED_EVENTS:
        return {"status": "ignored", "event": x_github_event}

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc

    # Idempotency: GitHub delivers at-least-once. The first POST records the delivery and
    # enqueues; any subsequent POST with the same X-GitHub-Delivery acks but does NOT re-enqueue,
    # so side effects (worker upserts, SSE republish, audit re-run) happen exactly once.
    if not x_github_delivery:
        raise HTTPException(status_code=400, detail="missing X-GitHub-Delivery header")
    newly_recorded = await try_record_delivery(
        session, delivery_id=x_github_delivery, event_type=x_github_event
    )
    if not newly_recorded:
        return {"status": "duplicate", "event": x_github_event, "delivery": x_github_delivery}

    await enqueue_event(x_github_event, payload)
    return {"status": "accepted", "event": x_github_event}
