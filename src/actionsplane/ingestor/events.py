"""Webhook payload normalization (plan §4, Phase 1).

Pure functions that map raw GitHub webhook JSON into the keyword args used to upsert ORM
rows. Kept free of I/O so they are trivially unit-testable against fixture payloads — the
repository layer is responsible for actually writing the rows.

Reference payloads:
  - workflow_run:  https://docs.github.com/webhooks/webhook-events-and-payloads#workflow_run
  - workflow_job:  https://docs.github.com/webhooks/webhook-events-and-payloads#workflow_job
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_repo(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract repo identity from any event that carries a ``repository`` block."""
    repo = payload["repository"]
    owner = repo["owner"]["login"]
    return {
        "id": repo["id"],
        "owner": owner,
        "name": repo["name"],
        "default_branch": repo.get("default_branch", "main"),
        "archived": repo.get("archived", False),
    }


def normalize_run_object(run: dict[str, Any], repo_id: int) -> dict[str, Any]:
    """Map a bare run object (REST ``/actions/runs`` or webhook ``workflow_run``) to row kwargs.

    Shared by the webhook path and the reconciliation poller so both produce identical rows.
    """
    return {
        "id": run["id"],
        "repo_id": repo_id,
        "workflow_id": run.get("workflow_id"),
        "run_number": run["run_number"],
        "head_branch": run.get("head_branch"),
        "head_sha": run.get("head_sha"),
        "event": run.get("event"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "created_at": _ts(run.get("created_at")),
        "started_at": _ts(run.get("run_started_at")),
        "completed_at": _ts(run.get("updated_at")) if run.get("conclusion") else None,
        # Monotonic per state transition — the ordering key for the out-of-order upsert guard.
        "updated_at": _ts(run.get("updated_at")),
        "actor": (run.get("actor") or {}).get("login"),
        "run_attempt": run.get("run_attempt", 1),
        "raw_payload": run,
    }


def normalize_workflow_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Map a ``workflow_run`` webhook event into workflow_runs row kwargs."""
    return normalize_run_object(payload["workflow_run"], payload["repository"]["id"])


def normalize_workflow_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Map a ``workflow_job`` event into workflow_jobs row kwargs."""
    job = payload["workflow_job"]
    return {
        "id": job["id"],
        "run_id": job["run_id"],
        "name": job.get("name"),
        "status": job.get("status"),
        "conclusion": job.get("conclusion"),
        "started_at": _ts(job.get("started_at")),
        "completed_at": _ts(job.get("completed_at")),
        "runner_name": job.get("runner_name"),
        "runner_group": job.get("runner_group_name"),
        "labels": job.get("labels", []),
        "raw_payload": job,
    }


def touches_workflows(payload: dict[str, Any]) -> bool:
    """For a ``push`` event: did it modify any ``.github/workflows/**`` file?

    Used to decide whether to re-parse a repo's workflow AST (drift/audit refresh).
    """
    prefix = ".github/workflows/"
    for commit in payload.get("commits", []):
        changed = (
            *commit.get("added", []),
            *commit.get("modified", []),
            *commit.get("removed", []),
        )
        for path in changed:
            if path.startswith(prefix):
                return True
    return False


def normalize_installation(payload: dict[str, Any]) -> dict[str, Any]:
    """Map an ``installation`` / ``installation_repositories`` event into installation kwargs."""
    inst = payload["installation"]
    account = inst.get("account") or {}
    return {
        "id": inst["id"],
        "account_login": account.get("login", ""),
        "account_type": account.get("type", "Organization"),
        "installed_at": _ts(inst.get("created_at")) or datetime.now(UTC),
    }


def installation_repos(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Repos granted to an installation. Owner is derived from ``full_name`` (owner/name).

    Handles both the ``installation`` event (``repositories``) and the
    ``installation_repositories`` event (``repositories_added``).
    """
    repos = payload.get("repositories") or payload.get("repositories_added") or []
    out: list[dict[str, Any]] = []
    for r in repos:
        full = r.get("full_name", "/")
        owner = full.split("/", 1)[0] if "/" in full else ""
        out.append(
            {
                "id": r["id"],
                "owner": owner,
                "name": r["name"],
                "default_branch": r.get("default_branch", "main"),
                "archived": r.get("archived", False),
            }
        )
    return out
