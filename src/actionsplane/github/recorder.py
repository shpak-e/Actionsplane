"""Cassette recorder for the live-validation write path (live-validation runbook §6).

Enabled by setting ``ACTIONSPLANE_RECORD_DIR``: every GitHub API exchange made through a
``GitHubClient`` is appended to that directory as one sanitized JSON file, so Stage E of live
validation leaves behind a contract-test corpus of *real* GitHub responses. Sanitization is
allowlist-based — a header not named below is dropped — so credentials (Authorization,
installation tokens) can never end up in a cassette by omission. Lab tooling: leave the setting
unset in normal operation.
"""

from __future__ import annotations

import json
import os
import re
from itertools import count
from pathlib import Path

import httpx

# Allowlists, not blocklists: anything absent here is dropped, so a new credential-bearing
# header is excluded by default rather than leaked by default.
_REQUEST_HEADERS = frozenset({"accept", "content-type", "if-none-match", "x-github-api-version"})
_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "etag",
        "link",
        "location",
        "retry-after",
        "x-github-request-id",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
    }
)
# Cassettes feed contract tests; beyond this a body adds bytes, not information.
_MAX_BODY_BYTES = 256 * 1024
_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")
_MARKER = "_actionsplane_recorder_installed"
# Process-wide sequence so concurrent clients recording into one directory never collide.
_SEQ = count(1)


def _body_text(raw: bytes) -> tuple[str | None, bool]:
    truncated = len(raw) > _MAX_BODY_BYTES
    return raw[:_MAX_BODY_BYTES].decode("utf-8", errors="replace") or None, truncated


def install_recorder(client: httpx.AsyncClient, record_dir: str) -> None:
    """Attach a response hook that appends sanitized exchanges to ``record_dir``.

    Idempotent per client — the factory hands the same ``httpx.AsyncClient`` to several
    ``GitHubClient`` instances during a sweep, and each exchange must be recorded once.
    """
    if getattr(client, _MARKER, False):
        return
    dir_path = Path(record_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()

    async def _record(response: httpx.Response) -> None:
        await response.aread()  # buffered — no GitHubClient call streams
        request = response.request
        req_body, req_truncated = _body_text(request.content)
        resp_body, resp_truncated = _body_text(response.content)
        record = {
            "method": request.method,
            "url": str(request.url),
            "status": response.status_code,
            "request": {
                "headers": {
                    k: v for k, v in request.headers.items() if k.lower() in _REQUEST_HEADERS
                },
                "body": req_body,
                "truncated": req_truncated,
            },
            "response": {
                "headers": {
                    k: v for k, v in response.headers.items() if k.lower() in _RESPONSE_HEADERS
                },
                "body": resp_body,
                "truncated": resp_truncated,
            },
        }
        slug = _SLUG_RE.sub("-", request.url.path).strip("-")[:80] or "root"
        name = f"{pid}-{next(_SEQ):04d}-{request.method.lower()}-{slug}.json"
        (dir_path / name).write_text(json.dumps(record, indent=2), encoding="utf-8")

    client.event_hooks["response"].append(_record)
    setattr(client, _MARKER, True)
