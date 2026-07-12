"""API authentication + minimal RBAC (plan §8, Phase 5.2).

Two bearer tokens gate ``/api/v1``:

* **operate** (``ACTIONSPLANE_API_TOKEN``) — full access, required by every mutating endpoint.
* **read** (``ACTIONSPLANE_API_READ_TOKEN``, optional) — read endpoints only; a write attempt
  with it answers 403.

When *neither* token is configured the API is open (local-dev convenience) and every caller is
treated as the operator — for READS. Every GitHub-writing / config-mutating endpoint instead uses
``require_configured_operate``, which fails closed: it refuses unless ``ACTIONSPLANE_API_TOKEN`` is
both configured *and* presented, so tokenless "open" mode can never reach a write path (review 3,
N1). Configuring only the read token likewise leaves writes unreachable (no operate token exists).
Token compares are constant-time. The actor label ("operate" | "read") flows into the write-audit
log so every mutation records *which* credential performed it. This is deliberately simple — two
shared tokens — and remains the seam where real OIDC/session auth would slot in later.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from actionsplane.config import get_settings

ACTOR_OPERATE = "operate"
ACTOR_READ = "read"


def _bearer_matches(expected: str, header: str | None) -> bool:
    """Constant-time compare of a configured token against a ``Bearer <token>`` header."""
    if not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(expected, header.removeprefix("Bearer "))


def token_ok(expected: str | None, header: str | None) -> bool:
    """True if no token is configured (open), or the bearer header matches in constant time."""
    if not expected:
        return True
    return _bearer_matches(expected, header)


def classify_actor(header: str | None, *, operate: str | None, read: str | None) -> str | None:
    """Pure RBAC core: map a bearer header to an actor label, or None (no valid credential).

    No tokens configured → open API, every caller is the operator (local dev). Otherwise the
    header must match one of the configured tokens; the operate token wins if both are set to
    the same value.
    """
    if not operate and not read:
        return ACTOR_OPERATE
    if operate and _bearer_matches(operate, header):
        return ACTOR_OPERATE
    if read and _bearer_matches(read, header):
        return ACTOR_READ
    return None


async def require_token(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency for read endpoints: accept either token; returns the actor label."""
    settings = get_settings()
    actor = classify_actor(authorization, operate=settings.api_token, read=settings.api_read_token)
    if actor is None:
        raise HTTPException(status_code=401, detail="missing or invalid API token")
    return actor


async def require_operate(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency for mutating endpoints: the operate token only (read token → 403)."""
    actor = await require_token(authorization)
    if actor != ACTOR_OPERATE:
        raise HTTPException(status_code=403, detail="read-only token cannot perform writes")
    return actor


async def require_configured_operate(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency for GitHub-writing / config-mutating endpoints — fail closed (N1).

    Stricter than ``require_operate``: it also refuses when *no* operate token is configured at
    all. Tokenless "open" mode is a convenience for reads only; a mutating endpoint must never be
    reachable without ``ACTIONSPLANE_API_TOKEN`` both configured and presented. Returns "operate".
    """
    if not get_settings().api_token:
        raise HTTPException(
            status_code=403,
            detail="writes require ACTIONSPLANE_API_TOKEN configured (open mode is read-only)",
        )
    return await require_operate(authorization)
