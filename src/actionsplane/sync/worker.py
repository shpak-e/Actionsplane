"""Async sync worker (plan §4, Phase 1).

Consumes events enqueued by the ingestor, persists them (event-sourced history), and runs
the polling reconciliation loop (every ``poll_interval_seconds``) that replays anything
webhooks dropped. Built on arq so workers are async-native and share the httpx client.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import httpx
from arq import cron
from arq.connections import RedisSettings

from actionsplane.audit.sarif_service import upload_repo_sarif
from actionsplane.audit.service import audit_repo
from actionsplane.config import get_settings
from actionsplane.db.base import get_sessionmaker
from actionsplane.db.repository import (
    create_binding,
    get_repo,
    list_repos,
    list_templates,
    update_binding_drift,
    upsert_installation,
    upsert_job,
    upsert_repo,
    upsert_run,
)
from actionsplane.drift import autobind_paths, compute_drift
from actionsplane.events import publish
from actionsplane.github.factory import TokenCache, app_jwt, client_for_installation
from actionsplane.ingestor import events
from actionsplane.observability import continue_trace, setup_tracing
from actionsplane.sync.concurrency import bounded_gather

log = logging.getLogger(__name__)


async def _maybe_upload_sarif(session, gh, repo) -> None:
    """Push the repo's findings to Code Scanning when opted in. A SARIF failure (e.g. missing
    ``security_events: write`` on some installations) is logged, never fatal to the audit."""
    if not get_settings().security_events_enabled:
        return
    try:
        await upload_repo_sarif(session, gh, repo)
    except Exception:
        log.warning("SARIF upload failed for %s/%s", repo.owner, repo.name, exc_info=True)


async def process_event(
    ctx: dict, event: str, payload: dict[str, Any], _trace: dict | None = None
) -> str:
    """Persist a normalized webhook event. Idempotent via upsert (at-least-once delivery).

    ``_trace`` is the W3C carrier injected by the ingestor; ``continue_trace`` makes this span a
    child of the ingest span so the whole webhook→persist flow is one trace.
    """
    with continue_trace(_trace, "worker.process_event", **{"event": event}):
        return await _process_event(ctx, event, payload)


async def _process_event(ctx: dict, event: str, payload: dict[str, Any]) -> str:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        if event == "workflow_run":
            repo = events.normalize_repo(payload)
            installation_id = (payload.get("installation") or {}).get("id")
            if installation_id is not None:
                await upsert_repo(session, repo, installation_id=installation_id)
            run = events.normalize_workflow_run(payload)
            await upsert_run(session, run)
            await publish("run", run)
        elif event == "workflow_job":
            job = events.normalize_workflow_job(payload)
            await upsert_job(session, job)
            await publish("job", job)
        elif event in ("installation", "installation_repositories"):
            inst = events.normalize_installation(payload)
            await upsert_installation(session, inst)
            for repo in events.installation_repos(payload):
                await upsert_repo(session, repo, installation_id=inst["id"])
        elif event == "push" and events.touches_workflows(payload):
            repo_id = payload["repository"]["id"]
            await ctx["redis"].enqueue_job("audit_repo_task", repo_id)
        await session.commit()
    return event


async def reconcile(ctx: dict) -> int:
    """Polling safety net: for each watched repo, replay recent runs via the REST API.

    Runs every ``poll_interval_seconds`` to recover from any dropped webhook deliveries.
    Upserts are idempotent, so re-ingesting an already-seen run is a no-op. Returns the
    number of runs reconciled. No-ops cleanly if the GitHub App isn't configured yet.
    """
    settings = get_settings()
    if not settings.github_app_id or not settings.github_app_private_key_path:
        return 0

    jwt = app_jwt()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        repos = await list_repos(session, watched_only=True)

    async def reconcile_one(repo, http, cache: TokenCache) -> int:
        gh = await client_for_installation(
            repo.installation_id, http=http, jwt=jwt, token_cache=cache
        )
        runs = await gh.list_workflow_runs(repo.owner, repo.name)
        async with sessionmaker() as s:
            for run in runs:
                await upsert_run(s, events.normalize_run_object(run, repo.id))
            await s.commit()
        return len(runs)

    async with httpx.AsyncClient(timeout=30) as http:
        cache: TokenCache = {}
        counts = await bounded_gather(
            [reconcile_one(r, http, cache) for r in repos],
            limit=settings.fetch_concurrency,
        )
    return sum(counts)


async def audit_all(ctx: dict) -> int:
    """Cron task: audit every watched repo's workflows. Returns total findings written."""
    settings = get_settings()
    if not settings.github_app_id or not settings.github_app_private_key_path:
        return 0
    jwt = app_jwt()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        repos = await list_repos(session, watched_only=True)

    async def audit_one(repo, http, cache: TokenCache) -> int:
        gh = await client_for_installation(
            repo.installation_id, http=http, jwt=jwt, token_cache=cache
        )
        async with sessionmaker() as s:
            written = await audit_repo(s, gh, repo)
            await _maybe_upload_sarif(s, gh, repo)
            return written

    async with httpx.AsyncClient(timeout=30) as http:
        cache: TokenCache = {}
        counts = await bounded_gather(
            [audit_one(r, http, cache) for r in repos],
            limit=settings.fetch_concurrency,
        )
    return sum(counts)


async def audit_repo_task(ctx: dict, repo_id: int) -> int:
    """Enqueued on push to .github/workflows/** — re-audit a single repo."""
    settings = get_settings()
    if not settings.github_app_id or not settings.github_app_private_key_path:
        return 0
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, httpx.AsyncClient(timeout=30) as http:
        repo = await get_repo(session, repo_id)
        if repo is None:
            return 0
        gh = await client_for_installation(repo.installation_id, http=http, jwt=app_jwt())
        written = await audit_repo(session, gh, repo)
        await _maybe_upload_sarif(session, gh, repo)
        return written


async def drift_sweep(ctx: dict) -> int:
    """Cron task: bind each repo's workflows to matching templates and score their drift.

    Returns the number of bindings checked. No-ops if the App or templates aren't configured.
    """
    settings = get_settings()
    if not settings.github_app_id or not settings.github_app_private_key_path:
        return 0
    jwt = app_jwt()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        templates = {t.name: t for t in await list_templates(session)}
        if not templates:
            return 0
        # snapshot the canonical YAML so concurrent tasks don't touch detached ORM objects
        canon = {name: (t.id, t.canonical_yaml) for name, t in templates.items()}
        repos = await list_repos(session, watched_only=True)

    async def drift_one(repo, http, cache: TokenCache) -> int:
        gh = await client_for_installation(
            repo.installation_id, http=http, jwt=jwt, token_cache=cache
        )
        paths = await gh.list_workflow_files(repo.owner, repo.name)
        bound = autobind_paths(list(canon), paths)
        n = 0
        async with sessionmaker() as s:
            for path, tpl_name in bound.items():
                tpl_id, tpl_yaml = canon[tpl_name]
                candidate = await gh.get_file_text(repo.owner, repo.name, path)
                report = compute_drift(tpl_yaml, candidate, path=path)
                binding = await create_binding(s, repo_id=repo.id, template_id=tpl_id, path=path)
                await update_binding_drift(s, binding, severity=report.severity.value)
                n += 1
            await s.commit()
        return n

    async with httpx.AsyncClient(timeout=30) as http:
        cache: TokenCache = {}
        counts = await bounded_gather(
            [drift_one(r, http, cache) for r in repos],
            limit=settings.fetch_concurrency,
        )
    return sum(counts)


async def _on_startup(ctx: dict) -> None:
    """Configure tracing once when the arq worker boots (so worker spans export too)."""
    setup_tracing("actionsplane-worker")


class WorkerSettings:
    """arq worker entrypoint: ``arq actionsplane.sync.worker.WorkerSettings``."""

    on_startup: ClassVar = _on_startup
    functions: ClassVar[list] = [process_event, audit_repo_task]
    cron_jobs: ClassVar[list] = [
        cron(reconcile, minute=set(range(0, 60, 5))),  # every 5 min
        cron(audit_all, hour=set(range(0, 24, 6))),  # every 6 h: org-wide audit sweep
        cron(drift_sweep, hour=set(range(0, 24, 6)), minute={30}),  # every 6 h: drift sweep
    ]

    # arq reads this as a RedisSettings *instance* (not a callable). Resolved at import from the
    # env-driven DSN, which is set before the arq worker loads WorkerSettings.
    redis_settings: ClassVar[RedisSettings] = RedisSettings.from_dsn(get_settings().redis_url)
