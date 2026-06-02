"""Tests for GitHub App authentication."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from actionsplane.github.app_auth import (
    InstallationToken,
    build_app_jwt,
    fetch_installation_token,
)


def _rsa_keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def test_build_app_jwt_claims():
    private_pem, public_pem = _rsa_keypair()
    token = build_app_jwt(123, private_pem, now=1_000_000)
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"], options={"verify_exp": False})
    assert decoded["iss"] == "123"
    assert decoded["iat"] == 1_000_000 - 60  # backdated for clock skew
    assert decoded["exp"] == decoded["iat"] + 600


def test_installation_token_expiry():
    future = datetime.now(UTC) + timedelta(hours=1)
    past = datetime.now(UTC) - timedelta(minutes=1)
    assert InstallationToken("t", future).is_expired() is False
    assert InstallationToken("t", past).is_expired() is True


@pytest.mark.asyncio
async def test_fetch_installation_token():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            201,
            json={"token": "ghs_abc", "expires_at": "2026-05-24T17:00:00Z"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_installation_token(
            "app.jwt.here", 999, client=client, api_url="https://api.github.com"
        )

    assert result.token == "ghs_abc"
    assert result.expires_at == datetime(2026, 5, 24, 17, 0, 0, tzinfo=UTC)
    assert captured["url"] == "https://api.github.com/app/installations/999/access_tokens"
    assert captured["auth"] == "Bearer app.jwt.here"
