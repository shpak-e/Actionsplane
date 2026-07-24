"""On-demand drift detail for a single binding (what actually drifted).

The worker's drift sweep stores only the *severity* on each binding; to answer "show me what
diverged" the dashboard needs the change list too. Rather than widen the schema and backfill, this
recomputes the diff on request: fetch the live candidate workflow via the installation client, diff
it against the stored canonical template, and return the engine's change list alongside both YAMLs
so the UI can render a side-by-side. Read-only — no writes, no PRs.
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.config import get_settings
from actionsplane.db.models import Repo, TemplateBinding, WorkflowTemplate
from actionsplane.drift.service import compute_drift
from actionsplane.github.factory import app_jwt, client_for_installation


async def drift_detail(session: AsyncSession, binding_id: int) -> dict:
    """Recompute the drift diff for one binding. Raises LookupError / RuntimeError on bad state."""
    binding = await session.get(TemplateBinding, binding_id)
    if binding is None:
        raise LookupError("binding not found")
    repo = await session.get(Repo, binding.repo_id)
    template = await session.get(WorkflowTemplate, binding.template_id)
    if repo is None or template is None:
        raise LookupError("binding references a missing repo or template")

    settings = get_settings()
    if not settings.github_app_id or not settings.github_app_private_key_path:
        raise RuntimeError("GitHub App is not configured — cannot fetch the live workflow")

    async with httpx.AsyncClient(timeout=30) as http:
        gh = await client_for_installation(repo.installation_id, http=http, jwt=app_jwt())
        candidate = await gh.get_file_text(repo.owner, repo.name, binding.path)

    report = compute_drift(template.canonical_yaml, candidate, path=binding.path)
    return {
        "binding_id": binding.id,
        "repo": f"{repo.owner}/{repo.name}",
        "path": binding.path,
        "template": template.name,
        "severity": report.severity.value,
        "changes": list(report.changes),
        "canonical_yaml": template.canonical_yaml,
        "candidate_yaml": candidate,
    }
