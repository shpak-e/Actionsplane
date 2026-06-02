"""API authentication (plan §8).

A minimal bearer-token gate for the read/write API. When ``ACTIONSPLANE_API_TOKEN`` is set,
every ``/api/v1`` request must send ``Authorization: Bearer <token>``; when it is unset the API
is open (local-dev convenience). The token compare is constant-time. This is deliberately simple
— a single shared token — and is the seam where real OIDC/session auth would slot in later.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from actionsplane.config import get_settings


def token_ok(expected: str | None, header: str | None) -> bool:
    """True if no token is configured (open), or the bearer header matches in constant time."""
    if not expected:
        return True
    if not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(expected, header.removeprefix("Bearer "))


async def require_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: enforce the bearer token when one is configured."""
    if not token_ok(get_settings().api_token, authorization):
        raise HTTPException(status_code=401, detail="missing or invalid API token")
