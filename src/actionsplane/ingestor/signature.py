"""Webhook signature verification (plan §8).

GitHub signs every webhook delivery with HMAC-SHA256 over the raw request body, keyed by
the App's webhook secret, and sends it in the ``X-Hub-Signature-256`` header. We verify
*every* inbound event before trusting it. Uses a constant-time compare to avoid timing
side-channels.
"""

from __future__ import annotations

import hashlib
import hmac

_PREFIX = "sha256="


def compute_signature(secret: str, body: bytes) -> str:
    """Return the expected ``sha256=...`` header value for a body."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{_PREFIX}{digest}"


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time verification of the ``X-Hub-Signature-256`` header."""
    if not header or not header.startswith(_PREFIX):
        return False
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected, header)
