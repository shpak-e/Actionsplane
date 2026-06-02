"""Tests for the workflow re-run path: GitHub client call + the executor service guard."""

from __future__ import annotations

import httpx
import pytest

from actionsplane.executor.actions import rerun_run
from actionsplane.github.client import GitHubClient


@pytest.mark.asyncio
async def test_client_rerun_posts_to_rerun_endpoint():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        return httpx.Response(201, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        await gh.rerun_run("acme", "infra", 5001)

    assert calls == [
        ("POST", "https://api.github.com/repos/acme/infra/actions/runs/5001/rerun"),
    ]


@pytest.mark.asyncio
async def test_client_rerun_raises_on_github_error():
    transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"message": "Forbidden"}))
    async with httpx.AsyncClient(transport=transport) as client:
        gh = GitHubClient("tok", client=client, api_url="https://api.github.com")
        with pytest.raises(httpx.HTTPStatusError):
            await gh.rerun_run("acme", "infra", 5001)


class _NoRunSession:
    """Minimal stand-in: get(WorkflowRun, id) -> None, exercising the not-found guard."""

    async def get(self, _model, _pk):
        return None


@pytest.mark.asyncio
async def test_service_raises_lookuperror_for_unknown_run():
    with pytest.raises(LookupError):
        await rerun_run(_NoRunSession(), 999)
