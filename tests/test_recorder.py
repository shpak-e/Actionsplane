"""Cassette recorder (live-validation runbook §6): sanitized capture, idempotent install,
settings gate. The sanitization assertions are the load-bearing ones — a cassette that leaks
an installation token would end up committed under tests/cassettes/.
"""

import json

import httpx
import pytest

from actionsplane.config import get_settings
from actionsplane.github.client import GitHubClient
from actionsplane.github.recorder import install_recorder


def _sha_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        text="a" * 40,
        headers={"ETag": 'W/"abc"', "X-RateLimit-Remaining": "4999"},
    )


@pytest.mark.asyncio
async def test_recorder_writes_sanitized_cassette(tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(_sha_handler)) as client:
        install_recorder(client, str(tmp_path))
        gh = GitHubClient("sekret-token", client=client, api_url="https://api.github.com")
        await gh.get_commit_sha("acme", "infra", "v4")

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    assert "sekret-token" not in raw
    assert "authorization" not in raw.lower()
    record = json.loads(raw)
    assert record["method"] == "GET"
    assert record["status"] == 200
    assert "/commits/" in record["url"]
    assert record["response"]["headers"]["etag"] == 'W/"abc"'
    assert record["response"]["body"] == "a" * 40


@pytest.mark.asyncio
async def test_recorder_install_is_idempotent(tmp_path):
    """The factory hands one httpx client to several GitHubClients — one exchange, one file."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(_sha_handler)) as client:
        install_recorder(client, str(tmp_path))
        install_recorder(client, str(tmp_path))
        assert len(client.event_hooks["response"]) == 1
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        await gh.get_commit_sha("acme", "infra", "v4")
    assert len(list(tmp_path.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_recorder_truncates_oversized_bodies(tmp_path):
    big = "x" * (300 * 1024)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=big))
    async with httpx.AsyncClient(transport=transport) as client:
        install_recorder(client, str(tmp_path))
        await client.get("https://api.github.com/huge")

    record = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert record["response"]["truncated"] is True
    assert len(record["response"]["body"]) == 256 * 1024


@pytest.mark.asyncio
async def test_client_installs_recorder_from_settings(tmp_path, monkeypatch):
    """ACTIONSPLANE_RECORD_DIR is the only switch — constructing a GitHubClient wires the hook."""
    monkeypatch.setenv("ACTIONSPLANE_RECORD_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_sha_handler)) as client:
            gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
            await gh.get_commit_sha("acme", "infra", "v4")
        assert len(list(tmp_path.glob("*.json"))) == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_recorder_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("ACTIONSPLANE_RECORD_DIR", raising=False)
    get_settings.cache_clear()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_sha_handler)) as client:
            gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
            await gh.get_commit_sha("acme", "infra", "v4")
            assert client.event_hooks["response"] == []
    finally:
        get_settings.cache_clear()
