"""Ingestor hardening: body-size cap, JSON guard, X-GitHub-Delivery dedup, and the
enqueue-before-record ordering that guarantees no acked-but-lost event (review 3, N4)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from actionsplane.config import get_settings
from actionsplane.db.base import get_session
from actionsplane.db.models import ProcessedDelivery  # noqa: F401 — registers the table
from actionsplane.ingestor import app as ingestor_module
from actionsplane.ingestor.signature import compute_signature

# SQLite doesn't have the postgres `on_conflict_do_nothing` syntax, so we test the dedup helpers
# at the *behavioural* level: monkeypatch delivery_seen / try_record_delivery to an in-memory set.

SECRET = "hunter2"


@pytest.fixture
def app(monkeypatch):
    seen: set[str] = set()
    enqueue_calls: list[tuple] = []
    control = {"fail_enqueue": False}

    async def fake_seen(_session, delivery_id: str) -> bool:
        return delivery_id in seen

    async def fake_record(_session, *, delivery_id: str, event_type: str | None) -> bool:
        if delivery_id in seen:
            return False
        seen.add(delivery_id)
        return True

    async def fake_enqueue(event: str, payload: dict, *, job_id: str | None = None) -> None:
        if control["fail_enqueue"]:
            raise RuntimeError("redis down")
        enqueue_calls.append((event, payload, job_id))

    monkeypatch.setattr(ingestor_module, "delivery_seen", fake_seen)
    monkeypatch.setattr(ingestor_module, "try_record_delivery", fake_record)
    monkeypatch.setattr(ingestor_module, "enqueue_event", fake_enqueue)

    # bypass DB by overriding the session dependency with a no-op
    async def fake_session():
        yield None

    ingestor_module.app.dependency_overrides[get_session] = fake_session

    # ensure the webhook secret is set for this test
    get_settings.cache_clear()
    monkeypatch.setenv("ACTIONSPLANE_GITHUB_WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()

    yield ingestor_module.app, enqueue_calls, seen, control

    ingestor_module.app.dependency_overrides.clear()
    get_settings.cache_clear()


def _post(client, body: bytes, event="workflow_run", delivery="d-1"):
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": compute_signature(SECRET, body),
            "Content-Type": "application/json",
        },
    )


def test_delivery_dedup_acks_but_does_not_re_enqueue(app):
    fa, enqueue_calls, _seen, _control = app
    body = json.dumps({"workflow_run": {"id": 1}, "repository": {"id": 9}}).encode()
    with TestClient(fa) as client:
        r1 = _post(client, body, delivery="abc")
        r2 = _post(client, body, delivery="abc")  # same delivery id
    assert r1.status_code == 200 and r1.json()["status"] == "accepted"
    assert r2.status_code == 200 and r2.json()["status"] == "duplicate"
    assert len(enqueue_calls) == 1  # second post did NOT re-enqueue side effects (single job)


def test_enqueue_uses_delivery_id_as_job_id(app):
    fa, enqueue_calls, _seen, _control = app
    body = json.dumps({"workflow_run": {"id": 2}, "repository": {"id": 9}}).encode()
    with TestClient(fa) as client:
        r = _post(client, body, delivery="job-42")
    assert r.status_code == 200
    assert enqueue_calls[-1][2] == "job-42"  # _job_id = X-GitHub-Delivery (arq's dedup key)


def test_enqueue_failure_does_not_record_delivery_then_retry_succeeds(app):
    fa, enqueue_calls, seen, control = app
    body = json.dumps({"workflow_run": {"id": 3}, "repository": {"id": 9}}).encode()
    with TestClient(fa) as client:
        control["fail_enqueue"] = True
        r1 = _post(client, body, delivery="x1")
        assert r1.status_code == 500
        assert "x1" not in seen  # enqueue failed BEFORE record → not acked → GitHub redelivers
        control["fail_enqueue"] = False
        r2 = _post(client, body, delivery="x1")  # the redelivery
        assert r2.status_code == 200 and r2.json()["status"] == "accepted"
    assert [c for c in enqueue_calls if c[2] == "x1"]  # the retry enqueued the (same) job


def test_oversize_body_rejected(app):
    fa, *_ = app
    huge = b"x" * (ingestor_module.MAX_BODY_BYTES + 1)
    with TestClient(fa) as client:
        # send with an honest Content-Length so the early-reject path fires
        r = client.post(
            "/webhook",
            content=huge,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": "d",
                "X-Hub-Signature-256": compute_signature(SECRET, huge),
                "Content-Type": "application/json",
                "Content-Length": str(len(huge)),
            },
        )
    assert r.status_code == 413


def test_invalid_json_rejected(app):
    fa, *_ = app
    body = b"{not json"
    with TestClient(fa) as client:
        r = _post(client, body)
    assert r.status_code == 400


def test_missing_delivery_header_rejected(app):
    fa, *_ = app
    body = json.dumps({"workflow_run": {"id": 1}, "repository": {"id": 9}}).encode()
    with TestClient(fa) as client:
        r = client.post(
            "/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": compute_signature(SECRET, body),
            },
        )
    assert r.status_code == 400


@pytest.mark.parametrize(
    "delivery",
    [
        "has space",
        "star*key",
        "colon:key",
        "line\nbreak",
        "x" * 65,  # over the 64-char bound
    ],
)
def test_malformed_delivery_header_rejected_before_enqueue(app, delivery):
    """The delivery id becomes a Redis key; a replayed delivery could vary it freely. Reject
    anything outside the safe opaque-token shape before it reaches Redis (review 4, NEW-5)."""
    fa, enqueue_calls, _seen, _control = app
    body = json.dumps({"workflow_run": {"id": 1}, "repository": {"id": 9}}).encode()
    with TestClient(fa) as client:
        r = client.post(
            "/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-GitHub-Delivery": delivery,
                "X-Hub-Signature-256": compute_signature(SECRET, body),
            },
        )
    assert r.status_code == 400
    assert enqueue_calls == []  # never reached the queue


def test_valid_uuid_delivery_accepted(app):
    """A real GitHub delivery GUID passes the shape check."""
    fa, enqueue_calls, _seen, _control = app
    body = json.dumps({"workflow_run": {"id": 1}, "repository": {"id": 9}}).encode()
    with TestClient(fa) as client:
        r = _post(client, body, delivery="72d3162e-cc78-11e3-81ab-4c9367dc0958")
    assert r.status_code == 200
    assert len(enqueue_calls) == 1
