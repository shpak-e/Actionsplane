"""SARIF orchestration — push a repo's open findings to GitHub Code Scanning (plan §13).

This is the I/O glue that turns the pure SARIF emitter (``audit/sarif.py``) into the find→fix
bridge: read the open findings for one repo, build the SARIF document, resolve the default-branch
head, and upload. Two entry points:

* ``upload_repo_sarif(session, gh, repo)`` — pure-ish: takes an injected client, so it's
  MockTransport + sqlite testable end-to-end without a real GitHub App.
* ``upload_sarif_for_repo(session, repo_id)`` — resolves the installation, mints a client, and
  delegates. Gated by ``security_events_enabled`` (mirrors ``bulk_edits_enabled``): off by
  default so the ``security_events: write`` scope is only exercised when the operator opts in.

An *empty* findings set is still uploaded on purpose — an analysis with no results is how Code
Scanning resolves alerts ActionsPlane previously raised but no longer sees (``partialFingerprints``
dedup), so a repo that got cleaned up doesn't keep stale alerts in the Security tab.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.audit.findings import Finding
from actionsplane.audit.sarif import findings_to_sarif
from actionsplane.config import get_settings
from actionsplane.db.models import AuditFinding, Repo
from actionsplane.db.repository import get_repo, open_findings
from actionsplane.github.client import GitHubClient
from actionsplane.github.factory import app_jwt, client_for_installation
from actionsplane.models.enums import FindingType, Severity
from actionsplane.observability import span

log = logging.getLogger(__name__)


def _row_to_finding(row: AuditFinding) -> Finding:
    """Reconstruct the pure ``Finding`` value object from a stored row (so the emitter is reused).

    Note: the SARIF fingerprint is recomputed from (type, ref) without ``path``; two findings of
    the same type+ref in *different* workflow files would dedup to one alert. In practice that
    pair is rare, and the stored DB fingerprint (which includes path) remains the source of truth.
    """
    return Finding(
        finding_type=FindingType(row.finding_type),
        severity=Severity(row.severity),
        message=row.message,
        ref=row.ref,
        path=row.path,
    )


async def upload_repo_sarif(
    session: AsyncSession,
    gh: GitHubClient,
    repo: Repo,
    *,
    ref: str | None = None,
    commit_sha: str | None = None,
) -> dict:
    """Build + upload SARIF for one repo's open findings. Returns GitHub's ``{id, url}``.

    Resolves the default-branch head SHA when ``commit_sha`` isn't supplied. Propagates
    ``httpx.HTTPStatusError`` if GitHub rejects the upload (e.g. missing scope).
    """
    with span("sarif.upload", **{"repo": f"{repo.owner}/{repo.name}"}) as s:
        rows = await open_findings(session, repo_id=repo.id)
        sarif = findings_to_sarif([_row_to_finding(r) for r in rows])
        branch = repo.default_branch or "main"
        if ref is None:
            ref = f"refs/heads/{branch}"
        if commit_sha is None:
            commit_sha = await gh.get_ref_sha(repo.owner, repo.name, branch)
        result = await gh.upload_sarif(repo.owner, repo.name, sarif, commit_sha=commit_sha, ref=ref)
        if s is not None:
            s.set_attribute("findings", len(rows))
        log.info("uploaded SARIF (%d findings) for %s/%s", len(rows), repo.owner, repo.name)
        return result


async def upload_sarif_for_repo(session: AsyncSession, repo_id: int) -> dict:
    """Resolve the installation, mint a client, and upload the repo's findings as SARIF.

    Raises ``PermissionError`` if Code Scanning integration isn't enabled, ``LookupError`` if the
    repo is unknown, and ``RuntimeError`` if the GitHub App isn't configured.
    """
    if not get_settings().security_events_enabled:
        raise PermissionError(
            "Code Scanning upload is disabled (set ACTIONSPLANE_SECURITY_EVENTS_ENABLED=true)"
        )
    repo = await get_repo(session, repo_id)
    if repo is None:
        raise LookupError(f"repo {repo_id} not found")

    jwt = app_jwt()  # raises RuntimeError if the GitHub App isn't configured
    async with httpx.AsyncClient(timeout=30) as http:
        gh = await client_for_installation(repo.installation_id, http=http, jwt=jwt)
        return await upload_repo_sarif(session, gh, repo)
