"""effective_redis_url injects the Redis password from a separate secret (review §4 H-1).

The URL can live in a ConfigMap while the credential lives in a Secret; the app stitches them
together. An already-credentialed URL is left alone.
"""

from __future__ import annotations

from actionsplane.config import Settings


def test_no_password_returns_url_unchanged():
    s = Settings(redis_url="redis://redis:6379/0")
    assert s.effective_redis_url == "redis://redis:6379/0"


def test_password_is_injected():
    s = Settings(redis_url="redis://redis:6379/0", redis_password="s3cr3t")
    assert s.effective_redis_url == "redis://:s3cr3t@redis:6379/0"


def test_password_is_url_encoded():
    s = Settings(redis_url="redis://redis:6379/0", redis_password="p@ss/w:rd")
    assert s.effective_redis_url == "redis://:p%40ss%2Fw%3Ard@redis:6379/0"


def test_existing_credentials_win():
    # If the operator already embedded a password in the URL, don't second-guess it.
    s = Settings(redis_url="redis://:already@redis:6379/0", redis_password="ignored")
    assert s.effective_redis_url == "redis://:already@redis:6379/0"


def test_preserves_db_and_username():
    s = Settings(redis_url="redis://user@redis:6380/3", redis_password="pw")
    assert s.effective_redis_url == "redis://user:pw@redis:6380/3"
