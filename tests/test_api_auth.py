"""API auth + RBAC (roadmap #10): env tokens, DB tokens, roles, and the token-admin endpoints."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.api import auth as auth_mod
from actionsplane.api.auth import (
    ACTOR_OPERATE,
    ACTOR_READ,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    classify_actor,
    generate_token,
    hash_token,
    require_configured_operate,
    require_identity,
    require_role,
    require_token,
    resolve_identity,
    token_ok,
)
from actionsplane.db.base import Base
from actionsplane.db.repository import create_api_token


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


class _Settings:
    def __init__(self, api_token=None, api_read_token=None):
        self.api_token = api_token
        self.api_read_token = api_read_token


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


# --- pure env-only helpers (retained) ------------------------------------------------------------


def test_token_ok_open_and_enforced():
    assert token_ok(None, "Bearer anything") is True  # unset → open
    assert token_ok("secret", "Bearer secret") is True
    assert token_ok("secret", "Bearer wrong") is False
    assert token_ok("secret", "secret") is False  # needs the Bearer prefix


def test_classify_actor_env_only():
    kw = {"operate": "op", "read": "ro"}
    assert classify_actor("Bearer op", **kw) == ACTOR_OPERATE
    assert classify_actor("Bearer ro", **kw) == ACTOR_READ
    assert classify_actor("Bearer nope", **kw) is None
    assert classify_actor(None, operate=None, read=None) == ACTOR_OPERATE  # open


def test_generate_and_hash_token():
    secret, digest, prefix = generate_token()
    assert secret.startswith("aptn_")
    assert digest == hash_token(secret) and len(digest) == 64
    assert secret.startswith(prefix) and len(prefix) == 12


# --- resolve_identity: env → DB → open -----------------------------------------------------------


async def test_env_tokens_map_to_admin_and_viewer(session, monkeypatch):
    monkeypatch.setattr(auth_mod, "get_settings", lambda: _Settings("op", "ro"))
    admin = await resolve_identity(session, "Bearer op")
    viewer = await resolve_identity(session, "Bearer ro")
    assert (admin.actor, admin.role) == (ACTOR_OPERATE, ROLE_ADMIN)
    assert (viewer.actor, viewer.role) == (ACTOR_READ, ROLE_VIEWER)
    # a wrong token with env configured is rejected (not open)
    assert await resolve_identity(session, "Bearer nope") is None
    assert await resolve_identity(session, None) is None


async def test_db_token_resolves_to_its_role(session, monkeypatch):
    monkeypatch.setattr(auth_mod, "get_settings", lambda: _Settings("op", None))
    secret, digest, prefix = generate_token()
    await create_api_token(
        session, name="ci-bot", role=ROLE_OPERATOR, token_hash=digest, prefix=prefix, created_by="t"
    )
    await session.commit()
    ident = await resolve_identity(session, f"Bearer {secret}")
    assert ident.role == ROLE_OPERATOR
    assert ident.actor == "token:ci-bot"


async def test_open_mode_is_viewer_only(session, monkeypatch):
    # nothing configured anywhere → open, but as a viewer (reads ok, writes blocked)
    monkeypatch.setattr(auth_mod, "get_settings", lambda: _Settings(None, None))
    ident = await resolve_identity(session, None)
    assert ident.role == ROLE_VIEWER
    # once a DB token exists, the API is no longer open: an anonymous request is rejected
    secret, digest, prefix = generate_token()
    await create_api_token(
        session, name="x", role=ROLE_ADMIN, token_hash=digest, prefix=prefix, created_by="t"
    )
    await session.commit()
    assert await resolve_identity(session, None) is None
    assert (await resolve_identity(session, f"Bearer {secret}")).role == ROLE_ADMIN


# --- dependencies + role gating ------------------------------------------------------------------


async def test_require_token_and_role_gates(session, monkeypatch):
    monkeypatch.setattr(auth_mod, "get_settings", lambda: _Settings("op", "ro"))
    admin = await require_identity(session=session, authorization="Bearer op")
    viewer = await require_identity(session=session, authorization="Bearer ro")
    assert await require_token(admin) == ACTOR_OPERATE
    # operator gate: admin passes, viewer 403s
    assert await require_configured_operate(admin) == ACTOR_OPERATE
    with pytest.raises(HTTPException) as exc:
        await require_configured_operate(viewer)
    assert exc.value.status_code == 403
    # admin gate: operator identity is refused
    op_only = auth_mod.Identity("token:ci", ROLE_OPERATOR)
    with pytest.raises(HTTPException) as exc:
        await require_role(ROLE_ADMIN)(op_only)
    assert exc.value.status_code == 403


async def test_require_identity_401_without_credential(session, monkeypatch):
    monkeypatch.setattr(auth_mod, "get_settings", lambda: _Settings("op", None))
    with pytest.raises(HTTPException) as exc:
        await require_identity(session=session, authorization=None)
    assert exc.value.status_code == 401


# --- end-to-end through the app (hermetic sqlite) ------------------------------------------------


@pytest.fixture
async def client(monkeypatch):
    """A TestClient with get_session backed by an isolated sqlite DB (no external DB needed)."""
    from fastapi.testclient import TestClient

    from actionsplane.api.app import app
    from actionsplane.db.base import get_session

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


def test_rbac_enforced_through_the_app(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "get_settings", lambda: _Settings("op-tok", "ro-tok"))
    ro = {"Authorization": "Bearer ro-tok"}
    op = {"Authorization": "Bearer op-tok"}
    assert client.get("/api/v1/mode", headers=ro).status_code == 200
    assert client.get("/api/v1/mode").status_code == 401  # no token at all
    assert client.post("/api/v1/offline/sync", headers=ro).status_code == 403  # viewer can't write
    assert client.post("/api/v1/offline/sync", headers=op).status_code == 409  # passes auth
    assert client.get("/api/v1/audit-log", headers=ro).status_code == 403


def test_writes_fail_closed_in_tokenless_mode(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "get_settings", lambda: _Settings(None, None))
    assert client.get("/api/v1/mode").status_code == 200  # reads stay open (viewer)
    for path in ("/api/v1/offline/sync", "/api/v1/runs/1/rerun"):
        assert client.post(path).status_code == 403, path
    assert client.get("/api/v1/audit-log").status_code == 403


def test_token_admin_endpoints(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "get_settings", lambda: _Settings("op-tok", "ro-tok"))
    admin = {"Authorization": "Bearer op-tok"}
    viewer = {"Authorization": "Bearer ro-tok"}

    # viewer cannot administer tokens
    assert client.get("/api/v1/access/tokens", headers=viewer).status_code == 403

    # admin mints a token; the secret is returned exactly once
    resp = client.post(
        "/api/v1/access/tokens",
        headers=admin,
        json={"name": "deploy-bot", "role": "operator"},
    )
    assert resp.status_code == 201
    created = resp.json()
    secret = created["secret"]
    assert secret.startswith("aptn_")
    assert "secret" not in created["token"]  # only metadata in the token object
    token_id = created["token"]["id"]

    # it shows up in the list (masked — prefix only, never the secret)
    listing = client.get("/api/v1/access/tokens", headers=admin).json()
    assert any(t["id"] == token_id and t["role"] == "operator" for t in listing)
    assert all(secret not in str(t) for t in listing)

    # the new operator token can write but not administer tokens
    op_bot = {"Authorization": f"Bearer {secret}"}
    assert client.post("/api/v1/offline/sync", headers=op_bot).status_code == 409  # write allowed
    assert client.get("/api/v1/access/tokens", headers=op_bot).status_code == 403  # admin-only

    # /access/me reflects the role
    me = client.get("/api/v1/access/me", headers=op_bot).json()
    assert me["role"] == "operator" and me["actor"] == "token:deploy-bot"

    # revoke it; afterwards it no longer authenticates
    assert client.delete(f"/api/v1/access/tokens/{token_id}", headers=admin).status_code == 200
    assert client.post("/api/v1/offline/sync", headers=op_bot).status_code == 401
    assert client.delete(f"/api/v1/access/tokens/{token_id}", headers=admin).status_code == 404


def test_create_token_rejects_bad_role(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "get_settings", lambda: _Settings("op-tok", None))
    resp = client.post(
        "/api/v1/access/tokens",
        headers={"Authorization": "Bearer op-tok"},
        json={"name": "x", "role": "superuser"},
    )
    assert resp.status_code == 422
