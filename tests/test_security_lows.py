"""Phase 5.0 security lows: operation validation/dispatch, PR-body escaping, owner/repo guard."""

from __future__ import annotations

import httpx
import pytest

from actionsplane.api.schemas import CampaignCreate
from actionsplane.executor.service import _md_inline, _resolve_pin_refs, dry_run_repo
from actionsplane.github.client import GitHubClient


# --- L-1: campaign.operation charset + registry dispatch --------------------------------------
def test_campaign_operation_rejects_bad_charset():
    with pytest.raises(ValueError, match="A-Za-z0-9"):
        CampaignCreate(name="x", operation="pin shas; rm -rf", repo_ids=[1])


def test_campaign_operation_accepts_known_shape():
    assert CampaignCreate(name="x", operation="pin-shas", repo_ids=[1]).operation == "pin-shas"


@pytest.mark.asyncio
async def test_dry_run_rejects_unimplemented_operation():
    # A registry-known-but-unimplemented op must raise, never silently fall through to pin-shas.
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
        gh = GitHubClient("tok", client=c, api_url="https://api.github.com")
        with pytest.raises(NotImplementedError):
            await dry_run_repo(gh, "acme", "infra", operation="set-permissions")


# --- L-2: AST-based pin resolver --------------------------------------------------------------
RUN_BLOCK_WF = (
    "name: ci\n"
    "on: [push]\n"
    "jobs:\n"
    "  build:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "      - run: |\n"
    "          cat <<'EOF' > /tmp/decoy.yml\n"
    "          uses: evil/exfiltrate@v1\n"
    "          EOF\n"
)


@pytest.mark.asyncio
async def test_resolver_ignores_uses_inside_run_block():
    """A `uses:` substring buried in untrusted run: content must not steer a GitHub API call at
    an attacker-chosen owner/repo — the resolver walks the parsed AST, not raw lines (L-2)."""
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        if "/commits/" in str(request.url):
            return httpx.Response(200, text="a" * 40)
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        gh = GitHubClient("tok", client=c, api_url="https://api.github.com")
        resolved = await _resolve_pin_refs(gh, RUN_BLOCK_WF, ".github/workflows/ci.yml")

    assert ("actions", "checkout", "v4") in resolved  # the real step ref is resolved
    assert not any("evil" in url for url in called)  # the decoy never reached the network


@pytest.mark.asyncio
async def test_resolver_skips_unparseable_workflow():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
        gh = GitHubClient("tok", client=c, api_url="https://api.github.com")
        assert await _resolve_pin_refs(gh, "- not\n- a\n- mapping\n", "bad.yml") == {}


# --- L-3: PR-body markdown escaping -----------------------------------------------------------
def test_md_inline_neutralizes_backticks_and_newlines():
    assert _md_inline("evil`code`\nmore") == "evil'code' more"
    assert "\n" not in _md_inline("a\r\nb")


# --- L-4: owner/repo charset guard ------------------------------------------------------------
@pytest.mark.asyncio
async def test_client_rejects_crafted_owner_repo():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
        gh = GitHubClient("tok", client=c, api_url="https://api.github.com")
        with pytest.raises(ValueError, match="invalid owner"):
            await gh.get_repo_meta("acme/../evil", "infra")
        with pytest.raises(ValueError, match="invalid repo"):
            await gh.list_workflow_files("acme", "infra?x=1")
