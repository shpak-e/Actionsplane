"""W7 require-check: ruleset client method + fail-closed gate."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import HTTPException

from actionsplane.github.client import GitHubClient
from actionsplane.governance.service import require_check_fleet


@pytest.mark.asyncio
async def test_create_required_check_ruleset_posts_expected_ruleset():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["json"] = json.loads(req.content)
        return httpx.Response(201, json={"id": 42, "name": "actionsplane: require build"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        gh = GitHubClient("tok", client=c, api_url="https://api.github.com")
        out = await gh.create_required_check_ruleset("acme", "api", check="build")

    assert out == {"id": 42, "name": "actionsplane: require build"}
    assert seen["url"].endswith("/repos/acme/api/rulesets")
    body = seen["json"]
    assert body["target"] == "branch" and body["enforcement"] == "active"
    assert body["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    rule = body["rules"][0]
    assert rule["type"] == "required_status_checks"
    assert rule["parameters"]["required_status_checks"] == [{"context": "build"}]


@pytest.mark.asyncio
async def test_require_check_fleet_fail_closed_when_bulk_edits_disabled():
    # Default test settings have bulk edits off → raise before any GitHub I/O (session unused).
    with pytest.raises(PermissionError, match="bulk edits"):
        await require_check_fleet(None, check="build", repo_ids=[1])  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_require_check_endpoint_maps_gate_to_403():
    from actionsplane.api.app import require_check_endpoint
    from actionsplane.api.schemas import RequireCheckIn

    with pytest.raises(HTTPException) as exc:
        await require_check_endpoint(
            RequireCheckIn(check="build", repo_ids=[1]),
            session=None,  # type: ignore[arg-type]
            actor="tester",
        )
    assert exc.value.status_code == 403
