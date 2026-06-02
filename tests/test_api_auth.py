"""Tests for the API bearer-token gate."""

from __future__ import annotations

from actionsplane.api.auth import token_ok


def test_open_when_no_token_configured():
    assert token_ok(None, None) is True
    assert token_ok(None, "Bearer anything") is True


def test_enforced_when_configured():
    assert token_ok("secret", "Bearer secret") is True
    assert token_ok("secret", "Bearer wrong") is False
    assert token_ok("secret", None) is False
    assert token_ok("secret", "secret") is False  # missing "Bearer " prefix
