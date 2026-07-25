"""API authentication + role-based access control (RBAC, roadmap #10).

Every ``/api/v1`` request resolves to an :class:`Identity` — an audit-log ``actor`` label plus a
``role`` (viewer < operator < admin). Two credential sources feed it, checked in order:

1. **Legacy env tokens** (unchanged, always win): ``ACTIONSPLANE_API_TOKEN`` → **admin**,
   ``ACTIONSPLANE_API_READ_TOKEN`` → **viewer**. Compared in constant time.
2. **Named DB tokens** (``api_tokens``): a presented bearer is SHA-256'd and looked up; the row's
   role is used. Admins mint/revoke these from the Access UI; only the hash is stored.

When *no* credential is configured anywhere (no env tokens, no DB tokens) the API is open for
local dev — but as a **viewer**, so reads work and every write still fails closed. Roles gate the
endpoints: reads need any identity, writes need ≥ operator, and token administration needs admin.

The pure env-only helpers (``classify_actor``/``token_ok``) are retained for callers/tests that
predate the DB layer; the request path goes through :func:`resolve_identity`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.config import get_settings
from actionsplane.db.base import get_session
from actionsplane.db.repository import count_api_tokens, get_api_token_by_hash

ACTOR_OPERATE = "operate"
ACTOR_READ = "read"
ACTOR_OPEN = "open"

ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"
ROLES = (ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN)
_RANK = {ROLE_VIEWER: 0, ROLE_OPERATOR: 1, ROLE_ADMIN: 2}


@dataclass(frozen=True, slots=True)
class Identity:
    """Who is making the request: an audit ``actor`` label and their ``role``."""

    actor: str
    role: str


# --- token helpers -------------------------------------------------------------------------------


def _bearer_matches(expected: str, header: str | None) -> bool:
    """Constant-time compare of a configured token against a ``Bearer <token>`` header."""
    if not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(expected, header.removeprefix("Bearer "))


def _bearer(header: str | None) -> str | None:
    if not header or not header.startswith("Bearer "):
        return None
    return header.removeprefix("Bearer ")


def hash_token(token: str) -> str:
    """The SHA-256 hex digest stored/looked-up for a DB token (the secret is never persisted)."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """Mint a new token: returns ``(secret, hash, prefix)``. The secret is shown to the operator
    exactly once; only the hash is stored, and the prefix is a non-secret display fragment."""
    secret = f"aptn_{secrets.token_urlsafe(32)}"
    return secret, hash_token(secret), secret[:12]


def token_ok(expected: str | None, header: str | None) -> bool:
    """True if no token is configured (open), or the bearer header matches in constant time.
    Env-only helper retained for pre-RBAC callers."""
    if not expected:
        return True
    return _bearer_matches(expected, header)


def classify_actor(header: str | None, *, operate: str | None, read: str | None) -> str | None:
    """Pure env-only actor classifier (pre-RBAC). Retained for compatibility; the request path
    uses :func:`resolve_identity`, which also consults the DB tokens."""
    if not operate and not read:
        return ACTOR_OPERATE
    if operate and _bearer_matches(operate, header):
        return ACTOR_OPERATE
    if read and _bearer_matches(read, header):
        return ACTOR_READ
    return None


# --- identity resolution -------------------------------------------------------------------------


async def resolve_identity(session: AsyncSession, authorization: str | None) -> Identity | None:
    """Resolve a request to an :class:`Identity`, or ``None`` when the credential is required but
    invalid. Env tokens win; then DB tokens; then open-mode (nothing configured) → viewer."""
    settings = get_settings()
    presented = _bearer(authorization)

    # 1. Legacy env tokens (constant-time), highest precedence — no DB touch on the common path.
    if (
        settings.api_token
        and presented is not None
        and hmac.compare_digest(settings.api_token, presented)
    ):
        return Identity(ACTOR_OPERATE, ROLE_ADMIN)
    if (
        settings.api_read_token
        and presented is not None
        and hmac.compare_digest(settings.api_read_token, presented)
    ):
        return Identity(ACTOR_READ, ROLE_VIEWER)

    # 2. Named DB tokens.
    if presented is not None:
        row = await get_api_token_by_hash(session, hash_token(presented))
        if row is not None:
            return Identity(f"token:{row.name}", row.role)

    # 3. Open mode: only when nothing is configured anywhere (env or DB). Reads work as a viewer;
    #    writes still fail closed (viewer < operator). A configured API with a bad/absent token
    #    lands here and returns None → 401.
    if (
        not settings.api_token
        and not settings.api_read_token
        and await count_api_tokens(session) == 0
    ):
        return Identity(ACTOR_OPEN, ROLE_VIEWER)
    return None


# --- FastAPI dependencies ------------------------------------------------------------------------


async def require_identity(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> Identity:
    """Resolve the caller's identity or 401. Used as the router-level read gate."""
    identity = await resolve_identity(session, authorization)
    if identity is None:
        raise HTTPException(status_code=401, detail="missing or invalid API token")
    return identity


async def require_token(identity: Identity = Depends(require_identity)) -> str:
    """Read gate: any valid identity. Returns the actor label."""
    return identity.actor


def require_role(minimum: str):
    """Build a dependency that requires ``minimum`` role or higher; returns the actor label."""

    async def _dep(identity: Identity = Depends(require_identity)) -> str:
        if _RANK[identity.role] < _RANK[minimum]:
            raise HTTPException(
                status_code=403,
                detail=f"this action requires the '{minimum}' role (you have '{identity.role}')",
            )
        return identity.actor

    return _dep


# Write endpoints need ≥ operator; token administration needs admin. ``require_configured_operate``
# keeps its name so the (many) existing write endpoints need no change — it now means "≥ operator",
# which in open mode is unreachable (the open identity is a viewer), preserving fail-closed writes.
require_operator = require_role(ROLE_OPERATOR)
require_configured_operate = require_operator
require_admin = require_role(ROLE_ADMIN)
