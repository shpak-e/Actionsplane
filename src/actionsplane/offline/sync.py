"""Offline mode: populate the read model from a fixed list of public GitHub repos.

No GitHub App, no webhooks, no live updates — just fetch each repo's recent runs and audit its
workflows over the public REST API (optionally with a plain ``ACTIONSPLANE_GITHUB_TOKEN`` for a
higher rate limit), on startup and whenever the user clicks **Sync**. The same audit engine and
upserts the App-mode worker uses run here, so the dashboard looks identical — just static.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.audit.service import audit_repo
from actionsplane.config import get_settings
from actionsplane.db.repository import (
    get_repo,
    upsert_installation,
    upsert_repo,
    upsert_run,
    upsert_workflow,
)
from actionsplane.github.client import GitHubClient
from actionsplane.ingestor import events

log = logging.getLogger(__name__)

# A synthetic installation to satisfy the repos.installation_id FK without a real GitHub App.
OFFLINE_INSTALLATION_ID = 0

# Last-sync state for the /mode endpoint + UI (single api process — good enough for this mode).
_last_sync: dict[str, object] = {"at": None, "repos": 0, "runs": 0, "findings": 0, "errors": []}


def last_sync() -> dict[str, object]:
    return dict(_last_sync)


def parse_repo_spec(spec: str) -> tuple[str, str] | None:
    """Accept ``owner/repo``, ``github.com/owner/repo``, or a full URL → ``(owner, repo)``."""
    s = spec.strip().rstrip("/")
    if s.endswith(".git"):
        s = s[: -len(".git")]
    if "github.com/" in s:
        s = s.split("github.com/", 1)[1]
    parts = s.strip("/").split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


async def sync_repo(
    session: AsyncSession, gh: GitHubClient, owner: str, name: str
) -> tuple[int, int]:
    """Pull one public repo: upsert the repo, its recent runs, and its audit findings.

    Returns ``(runs, findings)``. The caller is responsible for the offline installation row
    (FK target) existing first. Pure I/O over the injected client, so it's MockTransport-testable.
    """
    meta = await gh.get_repo_meta(owner, name)
    await upsert_repo(
        session,
        {
            "id": meta["id"],
            "owner": owner,
            "name": name,
            "default_branch": meta.get("default_branch", "main"),
            "archived": meta.get("archived", False),
        },
        installation_id=OFFLINE_INSTALLATION_ID,
    )
    await session.commit()
    repo = await get_repo(session, meta["id"])

    runs = 0
    for run in await gh.list_workflow_runs(owner, name):
        wf = events.workflow_ref_from_run(run, repo.id)  # FK parent before the run
        if wf is not None:
            await upsert_workflow(session, wf)
        await upsert_run(session, events.normalize_run_object(run, repo.id))
        runs += 1
    await session.commit()

    findings = await audit_repo(session, gh, repo)
    return runs, findings


async def sync_offline(session: AsyncSession) -> dict[str, object]:
    """Fetch + persist runs and audit findings for every configured offline repo.

    Idempotent (everything upserts). Per-repo failures are recorded but don't abort the sweep,
    so one private/renamed/rate-limited repo can't blank the whole dashboard.
    """
    settings = get_settings()
    specs = [s for s in (parse_repo_spec(r) for r in settings.offline_repo_list) if s]

    await upsert_installation(
        session,
        {
            "id": OFFLINE_INSTALLATION_ID,
            "account_login": "offline",
            "account_type": "Organization",
            "installed_at": datetime.now(UTC),
        },
    )
    await session.commit()

    runs_total = 0
    findings_total = 0
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=30) as http:
        gh = GitHubClient(settings.github_token, client=http)  # token may be None → public reads
        for owner, name in specs:
            try:
                runs, findings = await sync_repo(session, gh, owner, name)
                runs_total += runs
                findings_total += findings
            except Exception as exc:  # one bad repo must not sink the whole sweep
                errors.append(f"{owner}/{name}: {str(exc)[:200]}")
                log.warning("offline sync failed for %s/%s: %s", owner, name, exc)

    _last_sync.update(
        at=datetime.now(UTC),
        repos=len(specs),
        runs=runs_total,
        findings=findings_total,
        errors=errors,
    )
    log.info(
        "offline sync done: %d repos, %d runs, %d findings, %d errors",
        len(specs),
        runs_total,
        findings_total,
        len(errors),
    )
    return last_sync()
