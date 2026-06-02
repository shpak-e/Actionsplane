"""Build an authenticated GitHubClient for a repo's installation (plan §4, §8).

Centralises the App-JWT → installation-token → client flow so the worker, audit service, and
campaign executor share one implementation. The token cache stores the full
:class:`InstallationToken` and refreshes it before expiry, so long-running sweeps don't 401
mid-run when a 1-hour token lapses.
"""

from __future__ import annotations

import asyncio

import httpx

from actionsplane.config import get_settings
from actionsplane.github.app_auth import (
    InstallationToken,
    build_app_jwt,
    fetch_installation_token,
    load_private_key,
)
from actionsplane.github.client import GitHubClient

# installation_id -> cached token (with expiry); refreshed when expired
TokenCache = dict[int, InstallationToken]

# Per-installation locks gate concurrent minting so N coroutines on a cold cache don't all
# call /access_tokens. A meta-lock guards lock creation itself.
_install_locks: dict[int, asyncio.Lock] = {}
_locks_meta = asyncio.Lock()


async def _lock_for(installation_id: int) -> asyncio.Lock:
    async with _locks_meta:
        if installation_id not in _install_locks:
            _install_locks[installation_id] = asyncio.Lock()
        return _install_locks[installation_id]


def app_jwt() -> str:
    settings = get_settings()
    if not settings.github_app_id or not settings.github_app_private_key_path:
        raise RuntimeError("GitHub App is not configured")
    return build_app_jwt(settings.github_app_id, load_private_key())


async def client_for_installation(
    installation_id: int,
    *,
    http: httpx.AsyncClient,
    jwt: str | None = None,
    token_cache: TokenCache | None = None,
) -> GitHubClient:
    """Return a client authed for one installation, reusing a cached token until it expires."""
    cached = token_cache.get(installation_id) if token_cache is not None else None
    if cached is None or cached.is_expired():
        # Serialize the mint per installation; second waiter re-checks the cache and skips.
        async with await _lock_for(installation_id):
            cached = token_cache.get(installation_id) if token_cache is not None else None
            if cached is None or cached.is_expired():
                cached = await fetch_installation_token(
                    jwt or app_jwt(), installation_id, client=http
                )
                if token_cache is not None:
                    token_cache[installation_id] = cached
    return GitHubClient(cached.token, client=http)
