"""Webhook ingestor (plan §4, Phase 1).

A deliberately thin FastAPI app: enforce a body-size cap, verify the HMAC, dedup the delivery
via ``X-GitHub-Delivery`` (so at-least-once delivery becomes effectively-once for side effects),
and hand the raw payload to the async worker. Persistence still happens in the worker — the
ingestor only writes one row (the dedup record), keeping its 10s budget comfortable.

Hardening from review-2: ``X-GitHub-Delivery`` dedup, body-size cap, ``json.loads`` guarded.

Dedup-window caveat (review 4, N4): the delivery id also becomes arq's ``_job_id``, but arq only
remembers a job id for ``keep_result`` (3600s). A manual GitHub "Redeliver" *days* later — after a
crash that landed between enqueue and ``try_record_delivery`` — could therefore slip past both the
DB dedup (never recorded) and arq's dedup (long expired) and double-process. It's bounded, not
eliminated: the worker's upserts are idempotent, so the worst case is a duplicate SSE tick and a
duplicate audit enqueue, never corrupt state.
"""

from __future__ import annotations

import json
import re

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.config import get_settings
from actionsplane.db.base import get_session
from actionsplane.db.repository import delivery_seen, try_record_delivery
from actionsplane.ingestor.signature import verify_signature
from actionsplane.observability import instrument_fastapi, setup_tracing
from actionsplane.sync.queue import enqueue_event

setup_tracing("actionsplane-ingestor")
app = FastAPI(title="ActionsPlane Ingestor", version="0.0.0")
instrument_fastapi(app)

# GitHub doc'd max is ~25MB; we cap well below that to bound memory per pod.
MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB

# X-GitHub-Delivery is signed *outside* the HMAC (GitHub signs the body, not headers), yet we use
# it verbatim as a Redis key (arq ``_job_id`` → ``arq:job:<id>``) and a DB dedup key. A replayed
# delivery could vary it freely, so bound its shape before use: GitHub sends a UUID, and this safe
# opaque-token charset admits that while rejecting Redis metacharacters, whitespace, control chars,
# and over-long values (review 4, NEW-5).
_DELIVERY_ID_RE = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")

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

    # Idempotency + no-lost-events (review 3, N4). GitHub delivers at-least-once. A known
    # redelivery is fast-acked here without re-running side effects.
    if not x_github_delivery:
        raise HTTPException(status_code=400, detail="missing X-GitHub-Delivery header")
    if not _DELIVERY_ID_RE.match(x_github_delivery):
        raise HTTPException(status_code=400, detail="malformed X-GitHub-Delivery header")
    if await delivery_seen(session, x_github_delivery):
        return {"status": "duplicate", "event": x_github_event, "delivery": x_github_delivery}

    # Enqueue BEFORE recording the delivery, keyed by the delivery id so arq dedups. Recording
    # first would let a crash (or an enqueue failure) between record and enqueue strand an *acked*
    # event that never gets processed. This way, if anything after the enqueue fails, GitHub
    # redelivers and we enqueue the *same* job id → one job, no lost event. Enqueue failure → 500.
    try:
        await enqueue_event(x_github_event, payload, job_id=x_github_delivery)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail="failed to enqueue; the event will be redelivered"
        ) from exc

    await try_record_delivery(session, delivery_id=x_github_delivery, event_type=x_github_event)
    return {"status": "accepted", "event": x_github_event, "delivery": x_github_delivery}
