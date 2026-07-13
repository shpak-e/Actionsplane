"""Async sync worker (plan §4, Phase 1).

Consumes events enqueued by the ingestor, persists them (event-sourced history), and runs
the polling reconciliation loop (every ``poll_interval_seconds``) that replays anything
webhooks dropped. Built on arq so workers are async-native and share the httpx client.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx
from arq import cron
from arq.connections import RedisSettings

from actionsplane.audit.sarif_service import upload_repo_sarif
from actionsplane.audit.service import audit_repo
from actionsplane.config import get_settings
from actionsplane.db.base import get_sessionmaker
from actionsplane.db.repository import (
    claim_lease,
    create_binding,
    get_repo,
    list_repos,
    list_templates,
    prune_deliveries,
    prune_job_payloads,
    prune_run_payloads,
    record_write_audit,
    update_binding_drift,
    upsert_installation,
    upsert_job,
    upsert_repo,
    upsert_run,
    upsert_runs,
)
from actionsplane.drift import autobind_paths, compute_drift
from actionsplane.events import publish
from actionsplane.github.client import GitHubClient
from actionsplane.github.factory import TokenCache, app_jwt, client_for_installation
from actionsplane.ingestor import events
from actionsplane.observability import continue_trace, setup_tracing
from actionsplane.sync.concurrency import bounded_gather

log = logging.getLogger(__name__)

# Lease holder identity: unique per worker process, stable within it (Phase 5.3).
_HOLDER = f"{socket.gethostname()}:{os.getpid()}"

# Hard ceiling on how long any single sweep may run (arq ``job_timeout``). Sweep leases are given
# a TTL above this so a slow-but-live holder never lets its lease lapse mid-sweep (review 3, 4d).
_SWEEP_JOB_TIMEOUT = 900  # seconds


async def _claim_sweep_lease(name: str, ttl_seconds: int, *, quiet: bool = False) -> bool:
    """Claim the single-flight lease for one cron sweep; False → another replica has it.

    Makes the sweeps safe at worker ``replicas > 1``: every replica's cron fires, but only the
    claimant proceeds. The TTL covers the tick (and clock skew between replicas) while expiring
    before the next one, so a crashed holder can never wedge a sweep permanently. ``quiet``
    suppresses the skip log for heartbeat self-refreshes (which own the lease and always succeed).
    """
    async with get_sessionmaker()() as session:
        ok = await claim_lease(
            session, name=f"sweep:{name}", holder=_HOLDER, ttl_seconds=ttl_seconds
        )
    if not ok and not quiet:
        log.info("skipping %s sweep: lease held by another worker", name)
    return ok


# Reconcile lease: a short TTL keeps a *crashed* holder from wedging the 5-min sweep for long,
# while a heartbeat re-claims often enough that a *live* holder never lapses mid-sweep — even
# though a sweep may run up to job_timeout (900s), far past the tick (review 4, NEW-8). This
# decouples the TTL from the sweep's duration, which was the flaw in the old fixed 1020s TTL.
_RECONCILE_LEASE_TTL = 180
_RECONCILE_HEARTBEAT = 60


@contextlib.asynccontextmanager
async def _sweep_lease(
    name: str, *, ttl_seconds: int, heartbeat_seconds: int | None = None
) -> AsyncIterator[bool]:
    """Hold a sweep lease for the duration of a ``with`` block, optionally heartbeating it.

    Yields True if this worker owns the lease (proceed) or False if another replica holds it
    (skip). When ``heartbeat_seconds`` is set, a background task self-refreshes the lease on that
    cadence so a long sweep can keep a short TTL — the refresh is a no-op cost (one tiny upsert)
    and always succeeds because the holder matches. The task is cancelled on exit.
    """
    if not await _claim_sweep_lease(name, ttl_seconds):
        yield False
        return
    beat: asyncio.Task | None = None
    if heartbeat_seconds:

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(heartbeat_seconds)
                await _claim_sweep_lease(name, ttl_seconds, quiet=True)  # self-refresh

        beat = asyncio.create_task(_heartbeat())
    try:
        yield True
    finally:
        if beat is not None:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat


class RateGate:
    """Sweep-wide circuit breaker on the per-install rate-limit budget (Phase 5.5).

    Each per-repo task ``note()``s its client after fetching; once any observed
    ``X-RateLimit-Remaining`` dips under the floor the gate trips, and tasks that haven't
    started yet return immediately. The sweep ends gracefully — no exception storm, and the
    skipped repos are simply covered by the next sweep (idempotent upserts make that free).
    """

    def __init__(self, floor: int) -> None:
        self.floor = floor
        self.tripped = False

    def note(self, gh: GitHubClient) -> None:
        if not self.tripped and gh.rate_budget.below(self.floor):
            self.tripped = True
            log.warning(
                "rate-limit budget low (remaining=%s < floor=%d): pausing sweep, "
                "remaining repos deferred to the next sweep",
                gh.rate_budget.remaining,
                self.floor,
            )


async def _maybe_upload_sarif(session, gh, repo) -> None:
    """Push the repo's findings to Code Scanning when opted in. A SARIF failure (e.g. missing
    ``security_events: write`` on some installations) is logged, never fatal to the audit."""
    if not get_settings().security_events_enabled:
        return
    try:
        result = await upload_repo_sarif(session, gh, repo)
        await record_write_audit(
            session,
            actor="worker",
            action="sarif.upload",
            target=f"{repo.owner}/{repo.name}",
            detail={"analysis_url": str(result.get("url", ""))},
        )
        await session.commit()
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
    Lease-guarded (single-flight across replicas) and rate-budget-gated.
    """
    settings = get_settings()
    if not settings.github_app_id or not settings.github_app_private_key_path:
        return 0
    # Short TTL (crashed holder recovers fast) + heartbeat (live holder never lapses mid-sweep),
    # instead of a fixed TTL sized to the worst-case sweep duration (review 4, NEW-8).
    async with _sweep_lease(
        "reconcile", ttl_seconds=_RECONCILE_LEASE_TTL, heartbeat_seconds=_RECONCILE_HEARTBEAT
    ) as held:
        if not held:
            return 0

        jwt = app_jwt()
        gate = RateGate(settings.rate_limit_floor)
        # Only ask GitHub for runs created within the lookback window (server-side filter), and cap
        # the walk — a reconcile is a dropped-webhook safety net, so it needs no deep history (4b).
        created_floor = (
            datetime.now(UTC) - timedelta(hours=settings.reconcile_lookback_hours)
        ).strftime(">=%Y-%m-%d")
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            repos = await list_repos(session, watched_only=True)

        async def reconcile_one(repo, http, cache: TokenCache) -> int:
            if gate.tripped:
                return 0  # budget exhausted mid-sweep; this repo waits for the next tick
            gh = await client_for_installation(
                repo.installation_id, http=http, jwt=jwt, token_cache=cache
            )
            runs = await gh.list_workflow_runs(
                repo.owner, repo.name, created=created_floor, max_runs=100
            )
            gate.note(gh)
            if not runs:
                return 0
            # Batch the repo's runs into one statement, and drop raw_payload: reconcile is a
            # dropped-webhook safety net, so it shouldn't store (or overwrite a webhook's) bulky
            # payload — the column is deferred and pruned anyway (review §5 H4).
            rows = []
            for run in runs:
                row = events.normalize_run_object(run, repo.id)
                row.pop("raw_payload", None)
                rows.append(row)
            async with sessionmaker() as s:
                await upsert_runs(s, rows)
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
    if not await _claim_sweep_lease("audit", ttl_seconds=3600):
        return 0
    jwt = app_jwt()
    gate = RateGate(settings.rate_limit_floor)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        repos = await list_repos(session, watched_only=True)

    async def audit_one(repo, http, cache: TokenCache) -> int:
        if gate.tripped:
            return 0
        gh = await client_for_installation(
            repo.installation_id, http=http, jwt=jwt, token_cache=cache
        )
        async with sessionmaker() as s:
            written = await audit_repo(s, gh, repo)
            await _maybe_upload_sarif(s, gh, repo)
            gate.note(gh)
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
    if not await _claim_sweep_lease("drift", ttl_seconds=3600):
        return 0
    jwt = app_jwt()
    gate = RateGate(settings.rate_limit_floor)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        templates = {t.name: t for t in await list_templates(session)}
        if not templates:
            return 0
        # snapshot the canonical YAML so concurrent tasks don't touch detached ORM objects
        canon = {name: (t.id, t.canonical_yaml) for name, t in templates.items()}
        repos = await list_repos(session, watched_only=True)

    async def drift_one(repo, http, cache: TokenCache) -> int:
        if gate.tripped:
            return 0
        gh = await client_for_installation(
            repo.installation_id, http=http, jwt=jwt, token_cache=cache
        )
        paths = await gh.list_workflow_files(repo.owner, repo.name)
        gate.note(gh)
        bound = autobind_paths(list(canon), paths)
        # Fetch + diff every candidate first (network), THEN write — so the DB connection isn't
        # held across GitHub I/O (review §5 M2), mirroring reconcile_one / the audit sweep.
        scored: list[tuple[str, int, str]] = []  # (path, template_id, severity)
        for path, tpl_name in bound.items():
            tpl_id, tpl_yaml = canon[tpl_name]
            candidate = await gh.get_file_text(repo.owner, repo.name, path)
            report = compute_drift(tpl_yaml, candidate, path=path)
            scored.append((path, tpl_id, report.severity.value))
        if not scored:
            return 0
        async with sessionmaker() as s:
            for path, tpl_id, severity in scored:
                binding = await create_binding(s, repo_id=repo.id, template_id=tpl_id, path=path)
                await update_binding_drift(s, binding, severity=severity)
            await s.commit()
        return len(scored)

    async with httpx.AsyncClient(timeout=30) as http:
        cache: TokenCache = {}
        counts = await bounded_gather(
            [drift_one(r, http, cache) for r in repos],
            limit=settings.fetch_concurrency,
        )
    return sum(counts)


async def prune_retention(ctx: dict) -> int:
    """Cron task: enforce the payload-retention policy (Phase 5.6). Returns rows pruned.

    Nulls ``raw_payload`` on runs/jobs older than ``raw_payload_retention_days`` (normalized
    columns stay — history remains queryable) and deletes ``processed_deliveries`` rows older
    than ``delivery_retention_days``. Both dimensions batch their writes so a large backlog
    never holds a long lock. Lease-guarded like the sweeps; 0 disables a dimension.
    """
    settings = get_settings()
    if settings.raw_payload_retention_days <= 0 and settings.delivery_retention_days <= 0:
        return 0
    if not await _claim_sweep_lease("prune", ttl_seconds=3600):
        return 0
    now = datetime.now(UTC)
    pruned = 0
    async with get_sessionmaker()() as session:
        if settings.raw_payload_retention_days > 0:
            cutoff = now - timedelta(days=settings.raw_payload_retention_days)
            pruned += await prune_run_payloads(session, cutoff=cutoff)
            pruned += await prune_job_payloads(session, cutoff=cutoff)
        if settings.delivery_retention_days > 0:
            cutoff = now - timedelta(days=settings.delivery_retention_days)
            pruned += await prune_deliveries(session, cutoff=cutoff)
    if pruned:
        log.info("retention pruning removed payloads/rows: %d", pruned)
    return pruned


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
        cron(prune_retention, hour={4}, minute={45}),  # daily: payload retention (Phase 5.6)
    ]

    # Explicit runtime bounds (review 3, 4d): sweeps over many repos can run minutes, so give jobs
    # a generous timeout; cap concurrency; keep results briefly for debuggability. Defaults left
    # implicit before this made a slow sweep's behaviour (and lease TTL sizing) unclear.
    job_timeout: ClassVar[int] = _SWEEP_JOB_TIMEOUT  # 900s
    max_jobs: ClassVar[int] = 10
    keep_result: ClassVar[int] = 3600  # seconds

    # arq reads this as a RedisSettings *instance* (not a callable). Resolved at import from the
    # env-driven DSN, which is set before the arq worker loads WorkerSettings.
    redis_settings: ClassVar[RedisSettings] = RedisSettings.from_dsn(
        get_settings().effective_redis_url
    )
