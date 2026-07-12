"""Tests for the API bearer-token gate + read/operate RBAC (Phase 5.2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from actionsplane.api.auth import (
    ACTOR_OPERATE,
    ACTOR_READ,
    classify_actor,
    require_operate,
    require_token,
    token_ok,
)


def test_open_when_no_token_configured():
    assert token_ok(None, None) is True
    assert token_ok(None, "Bearer anything") is True


def test_enforced_when_configured():
    assert token_ok("secret", "Bearer secret") is True
    assert token_ok("secret", "Bearer wrong") is False
    assert token_ok("secret", None) is False
    assert token_ok("secret", "secret") is False  # missing "Bearer " prefix


def test_classify_open_api_treats_everyone_as_operator():
    # no tokens configured → local-dev open mode, caller is the operator
    assert classify_actor(None, operate=None, read=None) == ACTOR_OPERATE
    assert classify_actor("Bearer whatever", operate=None, read=None) == ACTOR_OPERATE


def test_classify_operate_and_read_tokens():
    kw = {"operate": "op-tok", "read": "ro-tok"}
    assert classify_actor("Bearer op-tok", **kw) == ACTOR_OPERATE
    assert classify_actor("Bearer ro-tok", **kw) == ACTOR_READ
    assert classify_actor("Bearer wrong", **kw) is None
    assert classify_actor(None, **kw) is None
    assert classify_actor("op-tok", **kw) is None  # missing "Bearer " prefix


def test_classify_read_only_config_is_fail_closed():
    # Only the read token configured: reads work, but nothing can ever classify as operate,
    # so the write paths are unreachable rather than open.
    assert classify_actor("Bearer ro-tok", operate=None, read="ro-tok") == ACTOR_READ
    assert classify_actor("Bearer ro-tok", operate="op-tok", read=None) is None


@pytest.fixture
def rbac_settings(monkeypatch):
    """Point the auth dependencies at a fixed operate+read token pair."""
    monkeypatch.setattr(
        "actionsplane.api.auth.get_settings",
        lambda: SimpleNamespace(api_token="op-tok", api_read_token="ro-tok"),
    )


async def test_require_token_accepts_either(rbac_settings):
    assert await require_token("Bearer op-tok") == ACTOR_OPERATE
    assert await require_token("Bearer ro-tok") == ACTOR_READ
    with pytest.raises(HTTPException) as exc:
        await require_token("Bearer nope")
    assert exc.value.status_code == 401


async def test_require_operate_rejects_read_token_with_403(rbac_settings):
    assert await require_operate("Bearer op-tok") == ACTOR_OPERATE
    with pytest.raises(HTTPException) as exc:
        await require_operate("Bearer ro-tok")
    assert exc.value.status_code == 403  # authenticated, but not authorized to write
    with pytest.raises(HTTPException) as exc:
        await require_operate(None)
    assert exc.value.status_code == 401


def test_rbac_enforced_through_the_app(rbac_settings):
    """End-to-end through FastAPI: read token reads, cannot write; operate token passes the
    RBAC gate (the offline-sync endpoint then 409s on offline mode being off — after auth)."""
    from fastapi.testclient import TestClient

    from actionsplane.api.app import app

    with TestClient(app) as client:
        ro = {"Authorization": "Bearer ro-tok"}
        op = {"Authorization": "Bearer op-tok"}
        assert client.get("/api/v1/mode", headers=ro).status_code == 200
        assert client.get("/api/v1/mode").status_code == 401  # no token at all
        assert client.post("/api/v1/offline/sync", headers=ro).status_code == 403
        assert client.post("/api/v1/offline/sync", headers=op).status_code == 409
        # the audit trail itself is operator-level information
        assert client.get("/api/v1/audit-log", headers=ro).status_code == 403
