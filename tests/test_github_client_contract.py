"""Offline contract tests: replay the Phase 5.1 live-validation cassettes through the client.

The cassettes in ``tests/cassettes/`` are real GitHub responses captured during live validation
(see that dir's README). Replaying them via ``httpx.MockTransport`` pins the client's write path —
the exact calls a campaign apply + SARIF upload make — against real payload shapes, with no live
App. If GitHub's response shape drifts, refresh the cassettes; if our parsing regresses, these fail.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from actionsplane.github.client import GitHubClient

_CASSETTES = Path(__file__).parent / "cassettes"


def _load(name: str) -> dict:
    return json.loads((_CASSETTES / f"{name}.json").read_text(encoding="utf-8"))


def _to_response(cassette: dict) -> httpx.Response:
    body = cassette["response"]["body"]
    return httpx.Response(
        cassette["status"],
        headers=cassette["response"]["headers"],
        content=b"" if body is None else body.encode("utf-8"),
    )


def _transport(*names: str) -> httpx.MockTransport:
    """Serve the named cassettes in order, asserting each request matches the recorded method+path.

    The path assertion means these tests also cover URL construction, not just response parsing.
    """
    cassettes = [_load(n) for n in names]
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        c = cassettes[min(state["i"], len(cassettes) - 1)]
        assert request.method == c["method"], f"expected {c['method']}, got {request.method}"
        assert urlsplit(str(request.url)).path == urlsplit(c["url"]).path
        state["i"] += 1
        return _to_response(c)

    return httpx.MockTransport(handler)


async def _client(*names: str):
    transport = _transport(*names)
    client = httpx.AsyncClient(transport=transport)
    return GitHubClient("ghs_tok", client=client, api_url="https://api.github.com"), client


@pytest.mark.asyncio
async def test_get_commit_sha_contract():
    gh, client = await _client("get-commit-sha-200")
    async with client:
        sha = await gh.get_commit_sha("actions", "checkout", "v4")
    assert sha == "11d5960a326750d5838078e36cf38b85af677262"
    assert len(sha) == 40


@pytest.mark.asyncio
async def test_get_ref_sha_contract():
    gh, client = await _client("get-ref-200")
    async with client:
        sha = await gh.get_ref_sha("shpak-e", "ap-lab-messy", "main")
    assert sha == "710c8cca65c454d4e6adae97fe0280483efda88a"


@pytest.mark.asyncio
async def test_get_file_text_contract():
    gh, client = await _client("get-file-text-200")
    async with client:
        text = await gh.get_file_text("shpak-e", "ap-lab-messy", ".github/workflows/ci.yml")
    assert "uses:" in text  # decoded the base64 contents payload into real YAML


@pytest.mark.asyncio
async def test_list_workflow_files_contract():
    gh, client = await _client("list-workflow-files-200")
    async with client:
        paths = await gh.list_workflow_files("shpak-e", "ap-lab-messy")
    assert paths == [".github/workflows/ci.yml"]


@pytest.mark.asyncio
async def test_list_workflow_files_304_returns_cached():
    """Conditional request: a primed ETag + a 304 must reuse the cached parse, not re-download."""
    gh, client = await _client("list-workflow-files-200", "list-workflow-files-304")
    async with client:
        first = await gh.list_workflow_files("shpak-e", "ap-lab-messy")
        second = await gh.list_workflow_files("shpak-e", "ap-lab-messy")
    assert first == second == [".github/workflows/ci.yml"]


@pytest.mark.asyncio
async def test_create_branch_contract():
    gh, client = await _client("create-branch-201")
    async with client:  # 201, no body to parse — just must not raise
        await gh.create_branch("shpak-e", "ap-lab-messy", "actionsplane/pin-shas-1", "710c8cca" * 5)


@pytest.mark.asyncio
async def test_put_file_contract():
    gh, client = await _client("put-file-200")
    async with client:
        await gh.put_file(
            "shpak-e",
            "ap-lab-messy",
            ".github/workflows/ci.yml",
            text="name: ci\n",
            message="ci: pin",
            branch="actionsplane/pin-shas-2",
            sha="1bbe36e8681c27c00b00cda5d3ab8f736903323f",
        )


@pytest.mark.asyncio
async def test_create_pull_request_contract():
    gh, client = await _client("open-pr-201")
    async with client:
        pr = await gh.create_pull_request(
            "shpak-e",
            "ap-lab-messy",
            head="actionsplane/pin-shas-2",
            base="main",
            title="ci: pin-shas-2",
            body="Automated by ActionsPlane",
        )
    assert pr["number"] == 1
    assert pr["html_url"] == "https://github.com/shpak-e/ap-lab-messy/pull/1"


@pytest.mark.asyncio
async def test_upload_sarif_contract():
    gh, client = await _client("sarif-upload-202")
    async with client:
        result = await gh.upload_sarif(
            "shpak-e",
            "ap-lab-messy",
            {"version": "2.1.0", "runs": []},
            commit_sha="710c8cca65c454d4e6adae97fe0280483efda88a",
            ref="refs/heads/main",
        )
    assert result["id"] == "1281eb60-8741-11f1-9e07-85e1a5bee05a"
    assert "code-scanning/sarifs/" in result["url"]


@pytest.mark.asyncio
async def test_put_file_403_without_workflows_permission():
    """The captured contract for an App missing the Workflows permission: the workflow-file PUT
    fails 403 'Resource not accessible by integration' (see .env.example / the cassettes README)."""
    gh, client = await _client("put-file-403-needs-workflows-perm")
    async with client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await gh.put_file(
                "shpak-e",
                "ap-lab-messy",
                ".github/workflows/ci.yml",
                text="name: ci\n",
                message="ci: pin",
                branch="actionsplane/pin-shas-2",
                sha="1bbe36e8681c27c00b00cda5d3ab8f736903323f",
            )
    assert exc.value.response.status_code == 403
