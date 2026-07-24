"""W1 — the immutable-pin class: a tag backed by a GitHub immutable release is treated as safe
(not flagged, not rewritten to a SHA), but only when proven immutable via the API."""

from __future__ import annotations

import httpx
import pytest

from actionsplane.audit.engine import audit_pins
from actionsplane.audit.immutable import resolve_immutable_refs
from actionsplane.audit.parser import parse_workflow
from actionsplane.audit.pins import classify, is_pinned_safely, ref_key
from actionsplane.executor.service import _resolve_pin_refs
from actionsplane.github.client import GitHubClient
from actionsplane.models.enums import FindingType, PinState

IMMUTABLE = frozenset({"actions/checkout@v4"})


def test_classify_tag_is_immutable_only_when_proven():
    assert classify("actions/checkout@v4").pin_state is PinState.TAG_PINNED  # default: not proven
    assert classify("actions/checkout@v4", immutable_refs=IMMUTABLE).pin_state is PinState.IMMUTABLE
    # A different tag on the same action is NOT covered by the set.
    assert (
        classify("actions/checkout@v3", immutable_refs=IMMUTABLE).pin_state is PinState.TAG_PINNED
    )


def test_is_pinned_safely_includes_immutable():
    assert not is_pinned_safely("actions/checkout@v4")
    assert is_pinned_safely("actions/checkout@v4", immutable_refs=IMMUTABLE)
    assert is_pinned_safely("actions/checkout@11d5960a326750d5838078e36cf38b85af677262")


WF = (
    "name: ci\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "      - uses: astral-sh/setup-uv@v5\n"
)


def test_audit_pins_skips_immutable_but_flags_the_rest():
    wf = parse_workflow(WF, "ci.yml")
    findings = audit_pins(wf, immutable_refs=IMMUTABLE)
    flagged = {f.ref for f in findings if f.finding_type is FindingType.UNPINNED_ACTION}
    assert "actions/checkout@v4" not in flagged  # proven immutable → not flagged
    assert "astral-sh/setup-uv@v5" in flagged  # still a mutable tag → flagged


def _release_and_commit_handler(immutable_tags: set[str]):
    """Route releases-by-tag (immutability) and commits (sha) for a MockTransport."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/releases/tags/" in path:
            owner, repo = path.split("/repos/")[1].split("/")[:2]
            tag = path.rsplit("/", 1)[1]
            if f"{owner}/{repo}@{tag}" in immutable_tags:
                return httpx.Response(200, json={"tag_name": tag, "immutable": True})
            return httpx.Response(404, json={"message": "Not Found"})
        if "/commits/" in path:
            return httpx.Response(200, text="a" * 40)
        return httpx.Response(404, json={})

    return handler


@pytest.mark.asyncio
async def test_is_immutable_release_reads_authoritative_flag():
    transport = httpx.MockTransport(_release_and_commit_handler({"actions/checkout@v4"}))
    async with httpx.AsyncClient(transport=transport) as c:
        gh = GitHubClient("tok", client=c, api_url="https://api.github.com")
        assert await gh.is_immutable_release("actions", "checkout", "v4") is True
        assert await gh.is_immutable_release("actions", "checkout", "v3") is False  # 404


@pytest.mark.asyncio
async def test_resolve_immutable_refs_returns_only_proven():
    transport = httpx.MockTransport(_release_and_commit_handler({"actions/checkout@v4"}))
    async with httpx.AsyncClient(transport=transport) as c:
        gh = GitHubClient("tok", client=c, api_url="https://api.github.com")
        got = await resolve_immutable_refs(
            gh, ["actions/checkout@v4", "astral-sh/setup-uv@v5", "actions/checkout@abc"]
        )
    assert got == frozenset({ref_key("actions", "checkout", "v4")})


@pytest.mark.asyncio
async def test_campaign_resolver_excludes_immutable_from_pin_map():
    """The pin-shas resolver must NOT resolve a SHA for an immutable tag, so the operation leaves
    it untouched (the dry-run/apply diff never rewrites a safe immutable tag)."""
    transport = httpx.MockTransport(_release_and_commit_handler({"actions/checkout@v4"}))
    async with httpx.AsyncClient(transport=transport) as c:
        gh = GitHubClient("tok", client=c, api_url="https://api.github.com")
        resolved = await _resolve_pin_refs(gh, WF, "ci.yml")
    assert ("actions", "checkout", "v4") not in resolved  # immutable → excluded
    assert ("astral-sh", "setup-uv", "v5") in resolved  # mutable → resolved for rewrite
