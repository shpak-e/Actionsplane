"""API middleware + config wiring: gzip on, CORS gated (review batch: P2 + CORS).

gzip is always installed (it compresses the JSON list responses); CORS is installed only when
origins are explicitly configured, so a token-open deployment stays same-origin by default.
"""

from __future__ import annotations

from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from actionsplane.api.app import app
from actionsplane.config import Settings


def test_gzip_middleware_installed():
    assert any(m.cls is GZipMiddleware for m in app.user_middleware)


def test_cors_off_by_default():
    # No origins configured for the running app (default settings) → no CORS middleware, so the
    # API is same-origin only. This is the safe default when the API can run without a token.
    assert Settings().cors_origin_list == []
    assert not any(m.cls is CORSMiddleware for m in app.user_middleware)


def test_cors_origin_list_parses_and_trims():
    s = Settings(cors_allow_origins=" https://a.example.com , https://b.example.com ,")
    assert s.cors_origin_list == ["https://a.example.com", "https://b.example.com"]
