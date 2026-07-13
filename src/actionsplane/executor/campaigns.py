"""Campaign orchestration (plan §5.4, Phase 4).

A campaign applies one operation across many repos, always via PRs. Lifecycle is dry-run-first:
``run_dry_run`` computes per-repo diffs (no writes) and records the resolved tag→SHA map on the
campaign; ``apply_campaign`` reuses that exact map so the SHAs that land match the reviewed diff.

Fail-closed safety: bulk apply requires both ``bulk_edits_enabled`` AND a configured
``api_token`` (so org-wide writes can't be triggered on an unauthenticated API). Per-target status
tracks each repo independently.
"""

from __future__ import annotations

import httpx

from actionsplane.config import get_settings
from actionsplane.db.models import Campaign
from actionsplane.db.repository import get_repo, list_targets
from actionsplane.executor.service import dry_run_repo, open_pr_for_edits
from actionsplane.github.factory import TokenCache, app_jwt, client_for_installation

# campaign.params resolved-map (de)serialization — JSONB needs string-friendly entries
_KEYSEP = "\t"


def _dump_resolved(resolved: dict[tuple[str, str, str], str]) -> list[list[str]]:
    return [[o, r, ref, sha] for (o, r, ref), sha in resolved.items()]


def _load_resolved(rows: list) -> dict[tuple[str, str, str], str]:
    return {(o, r, ref): sha for o, r, ref, sha in rows}


async def run_dry_run(session, campaign: Campaign) -> Campaign:
    """Compute per-repo diffs for the campaign and store them + the resolved SHAs. No writes."""
    jwt = app_jwt()
    resolved_all: dict[tuple[str, str, str], str] = {}
    async with httpx.AsyncClient(timeout=30) as http:
        cache: TokenCache = {}
        for target in await list_targets(session, campaign_id=campaign.id):
            repo = await get_repo(session, target.repo_id)
            if repo is None:
                target.status, target.error = "failed", "repo not found"
                continue
            try:
                gh = await client_for_installation(
                    repo.installation_id, http=http, jwt=jwt, token_cache=cache
                )
                edits, resolved = await dry_run_repo(
                    gh, repo.owner, repo.name, operation=campaign.operation
                )
                resolved_all.update(resolved)
                target.diff_preview = "\n".join(e.diff for e in edits) or None
                target.status = "dry-run-ok"
            except Exception as exc:  # one repo failing shouldn't abort the campaign
                target.status, target.error = "failed", str(exc)[:500]
    campaign.params = {**(campaign.params or {}), "resolved": _dump_resolved(resolved_all)}
    campaign.status = "dry-run-ok"
    await session.commit()
    return campaign


async def apply_campaign(session, campaign: Campaign) -> Campaign:
    """Open PRs for each dry-run-ok target, reusing the resolved SHAs from the dry-run.

    Requires bulk edits enabled AND an API token configured (fail-closed).
    """
    settings = get_settings()
    if not settings.bulk_edits_enabled:
        raise PermissionError("bulk edits are disabled (set ACTIONSPLANE_BULK_EDITS_ENABLED=true)")
    if not settings.api_token:
        raise PermissionError("bulk edits require ACTIONSPLANE_API_TOKEN to be configured")

    resolved = _load_resolved((campaign.params or {}).get("resolved", []))
    jwt = app_jwt()
    rationale = f"Automated by ActionsPlane campaign `{campaign.name}` ({campaign.operation})."
    async with httpx.AsyncClient(timeout=30) as http:
        cache: TokenCache = {}
        for target in await list_targets(session, campaign_id=campaign.id):
            if target.status != "dry-run-ok" or not target.diff_preview:
                continue
            repo = await get_repo(session, target.repo_id)
            if repo is None:
                continue
            try:
                gh = await client_for_installation(
                    repo.installation_id, http=http, jwt=jwt, token_cache=cache
                )
                # reuse the previewed SHA map — no fresh HEAD resolution
                edits, _ = await dry_run_repo(
                    gh, repo.owner, repo.name, operation=campaign.operation, resolved=resolved
                )
                if not edits:
                    continue
                pr = await open_pr_for_edits(
                    gh,
                    repo.owner,
                    repo.name,
                    edits,
                    base_branch=repo.default_branch,
                    operation_id=f"{campaign.operation}-{campaign.id}",
                    rationale=rationale,
                )
                target.pr_number, target.pr_url, target.status = (
                    pr["number"],
                    pr["html_url"],
                    "pr-opened",
                )
            except Exception as exc:
                target.status, target.error = "conflict", str(exc)[:500]
    campaign.status = "applied"
    await session.commit()
    return campaign
