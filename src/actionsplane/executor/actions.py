"""Workflow-run actions — the "act on a run" write path (re-run).

Mirrors the campaign executor's client construction: resolve the run's repo + installation,
mint an installation-scoped client, and call GitHub. Kept thin and side-effect-isolated so the
API endpoint stays a one-liner. Requires the GitHub App to be configured and the installation to
grant ``actions: write`` (a scope beyond the read-only observe path — see plan §8).
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.db.models import WorkflowRun
from actionsplane.db.repository import get_repo
from actionsplane.github.factory import app_jwt, client_for_installation


async def rerun_run(session: AsyncSession, run_id: int) -> None:
    """Re-run a stored workflow run on GitHub.

    Raises ``LookupError`` if the run or its repo is unknown, ``RuntimeError`` if the App isn't
    configured, and propagates ``httpx.HTTPStatusError`` if GitHub rejects the request.
    """
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        raise LookupError(f"run {run_id} not found")
    repo = await get_repo(session, run.repo_id)
    if repo is None:
        raise LookupError(f"repo {run.repo_id} not found")

    jwt = app_jwt()  # raises RuntimeError if the GitHub App isn't configured
    async with httpx.AsyncClient(timeout=30) as http:
        gh = await client_for_installation(repo.installation_id, http=http, jwt=jwt)
        await gh.rerun_run(repo.owner, repo.name, run_id)
