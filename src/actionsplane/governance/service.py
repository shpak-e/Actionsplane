"""Require-check fleet action (W7): deploy a required status check to many repos.

The one deliberate exception to "every edit is a reviewable PR": branch protection / rulesets are
set via the GitHub API, not a workflow-file PR. So this is a **gated direct write** — fail-closed
behind the same ``bulk_edits_enabled`` + ``api_token`` gate as campaigns, always audited, and
**optional**: it needs the App's ``administration: write`` permission (org-admin-level), which many
orgs won't grant. When it isn't granted the per-repo write 403s and we surface exactly that, rather
than the feature vanishing. Uses the additive rulesets API, so existing protection is untouched.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.config import get_settings
from actionsplane.db.repository import get_repo
from actionsplane.github.factory import TokenCache, app_jwt, client_for_installation

log = logging.getLogger(__name__)


async def require_check_fleet(
    session: AsyncSession, *, check: str, repo_ids: list[int]
) -> list[dict]:
    """Require ``check`` on each repo's default branch. Returns a per-repo result list.

    Raises ``PermissionError`` (gate closed) / ``RuntimeError`` (no App) before touching GitHub;
    per-repo failures (e.g. missing ``administration: write``) go in the result, not raised.
    """
    settings = get_settings()
    if not settings.bulk_edits_enabled:
        raise PermissionError("bulk edits are disabled (set ACTIONSPLANE_BULK_EDITS_ENABLED=true)")
    if not settings.api_token:
        raise PermissionError("require-check needs ACTIONSPLANE_API_TOKEN to be configured")
    if not settings.github_app_id or not settings.github_app_private_key_path:
        raise RuntimeError("GitHub App not configured — require-check needs installation access")

    jwt = app_jwt()
    results: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as http:
        cache: TokenCache = {}
        for rid in repo_ids:
            repo = await get_repo(session, rid)
            if repo is None:
                results.append({"repo_id": rid, "status": "error", "detail": "repo not found"})
                continue
            slug = f"{repo.owner}/{repo.name}"
            try:
                gh = await client_for_installation(
                    repo.installation_id, http=http, jwt=jwt, token_cache=cache
                )
                rs = await gh.create_required_check_ruleset(repo.owner, repo.name, check=check)
                results.append(
                    {"repo_id": rid, "repo": slug, "status": "required", "ruleset_id": rs["id"]}
                )
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                detail = (
                    "needs the App's administration:write permission"
                    if code == 403
                    else f"GitHub returned {code}"
                )
                results.append({"repo_id": rid, "repo": slug, "status": "error", "detail": detail})
            except Exception as exc:  # one repo failing must not abort the fleet action
                results.append(
                    {"repo_id": rid, "repo": slug, "status": "error", "detail": str(exc)[:200]}
                )
    log.info("require-check %r across %d repos", check, len(repo_ids))
    return results
