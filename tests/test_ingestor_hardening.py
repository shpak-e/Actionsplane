"""Ingestor hardening (review-2): body-size cap, JSON guard, X-GitHub-Delivery dedup."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from actionsplane.config import get_settings
from actionsplane.db.base import get_session
from actionsplane.db.models import ProcessedDelivery  # noqa: F401 — registers the table
from actionsplane.ingestor import app as ingestor_module
from actionsplane.ingestor.signature import compute_signature

# SQLite doesn't have the postgres `on_conflict_do_nothing` syntax, so we test the dedup helper
# at the *behavioural* level: monkeypatch try_record_delivery to a deterministic in-memory set.

SECRET = "hunter2"


@pytest.fixture
def app(monkeypatch):
    seen: set[str] = set()

    async def fake_record(_session, *, delivery_id: str, event_type: str | None) -> bool:
        if delivery_id in seen:
            return False
        seen.add(delivery_id)
        return True

    enqueue_calls: list[tuple[str, dict]] = []

    async def fake_enqueue(event: str, payload: dict) -> None:
        enqueue_calls.append((event, payload))

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

    yield ingestor_module.app, enqueue_calls

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
    fa, enqueue_calls = app
    body = json.dumps({"workflow_run": {"id": 1}, "repository": {"id": 9}}).encode()
    with TestClient(fa) as client:
        r1 = _post(client, body, delivery="abc")
        r2 = _post(client, body, delivery="abc")  # same delivery id
    assert r1.status_code == 200 and r1.json()["status"] == "accepted"
    assert r2.status_code == 200 and r2.json()["status"] == "duplicate"
    assert len(enqueue_calls) == 1  # second post did NOT re-enqueue side effects


def test_oversize_body_rejected(app):
    fa, _ = app
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
    fa, _ = app
    body = b"{not json"
    with TestClient(fa) as client:
        r = _post(client, body)
    assert r.status_code == 400


def test_missing_delivery_header_rejected(app):
    fa, _ = app
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
