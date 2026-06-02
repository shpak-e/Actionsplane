"""Tests for webhook HMAC signature verification."""

from __future__ import annotations

from actionsplane.ingestor.signature import compute_signature, verify_signature

SECRET = "it's-a-secret-to-everybody"
BODY = b'{"action":"completed"}'


def test_roundtrip() -> None:
    header = compute_signature(SECRET, BODY)
    assert header.startswith("sha256=")
    assert verify_signature(SECRET, BODY, header) is True


def test_rejects_tampered_body() -> None:
    header = compute_signature(SECRET, BODY)
    assert verify_signature(SECRET, b'{"action":"opened"}', header) is False


def test_rejects_wrong_secret() -> None:
    header = compute_signature("other-secret", BODY)
    assert verify_signature(SECRET, BODY, header) is False


def test_rejects_missing_or_malformed_header() -> None:
    assert verify_signature(SECRET, BODY, None) is False
    assert verify_signature(SECRET, BODY, "deadbeef") is False
