"""GitHub App authentication (plan §4, §8).

We authenticate as a GitHub App (not a PAT): mint a short-lived JWT signed with the App's
private key, exchange it for a per-installation token, and use that for REST/GraphQL calls.
This gives per-install scoping, webhooks, and no user-token sprawl.

The flow:
    app JWT (RS256, ~10 min)  ->  POST /app/installations/{id}/access_tokens  ->  token (1h)
Installation tokens are cached until shortly before expiry so we don't mint one per call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import jwt

from actionsplane.config import get_settings

# Refresh a little before the real expiry to avoid races on long requests.
_EXPIRY_SKEW_SECONDS = 60


def build_app_jwt(app_id: int, private_key_pem: str, *, now: int | None = None) -> str:
    """Mint a ~10-minute app JWT (used only to fetch installation tokens).

    ``iat`` is backdated 60s to tolerate clock skew, per GitHub's guidance.
    """
    issued = (now or int(time.time())) - 60
    payload = {"iat": issued, "exp": issued + 600, "iss": str(app_id)}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


@dataclass(frozen=True, slots=True)
class InstallationToken:
    token: str
    expires_at: datetime

    def is_expired(self, *, at: datetime | None = None) -> bool:
        now = at or datetime.now(UTC)
        return now.timestamp() >= self.expires_at.timestamp() - _EXPIRY_SKEW_SECONDS


def _parse_expiry(value: str) -> datetime:
    # GitHub returns RFC3339 like "2026-05-24T17:00:00Z".
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def fetch_installation_token(
    app_jwt: str,
    installation_id: int,
    *,
    client: httpx.AsyncClient,
    api_url: str | None = None,
) -> InstallationToken:
    """Exchange an app JWT for an installation access token."""
    base = (api_url or get_settings().github_api_url).rstrip("/")
    resp = await client.post(
        f"{base}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return InstallationToken(token=data["token"], expires_at=_parse_expiry(data["expires_at"]))


def load_private_key() -> str:
    """Read the App private key from the path in settings (never inlined in env)."""
    settings = get_settings()
    if not settings.github_app_private_key_path:
        raise RuntimeError("ACTIONSPLANE_GITHUB_APP_PRIVATE_KEY_PATH is not configured")
    with open(settings.github_app_private_key_path, encoding="utf-8") as fh:
        return fh.read()
