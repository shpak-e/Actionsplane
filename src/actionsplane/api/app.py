"""REST API (plan §4, Phase 1).

Serves the read model the React UI and CLI consume: repos, workflows, runs, and per-workflow
metrics. GraphQL (for efficient cross-repo queries) is layered on in a later step; REST covers
the resource reads now. Endpoints are thin: repository query -> response model.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from actionsplane import __version__
from actionsplane.api.auth import require_configured_operate, require_token
from actionsplane.api.schemas import (
    AdvisoryReportOut,
    AuditLogEntryOut,
    BindingCreate,
    BindingOut,
    CampaignCreate,
    CampaignOut,
    CampaignRepoProgress,
    CampaignSummaryOut,
    CampaignTargetOut,
    CampaignVerification,
    CostSuggestionOut,
    DriftDetailOut,
    FindingOut,
    FindingsPage,
    FleetCostOut,
    FleetCostRow,
    JobOut,
    MetricsOut,
    ModeOut,
    NotificationChannelIn,
    NotificationChannelOut,
    NotificationChannelUpdate,
    NotificationEventOut,
    NotificationTestOut,
    PipelineGraphOut,
    PolicySimulateIn,
    RadarReportOut,
    RepoAddIn,
    RepoOut,
    RequireCheckIn,
    RequireCheckOut,
    RunOut,
    SarifIngestIn,
    SarifIngestOut,
    ScorecardOut,
    SimulationReportOut,
    TemplateCreate,
    TemplateOut,
    WorkflowOut,
)
from actionsplane.audit.advisory_watch import scan_fleet as advisory_scan_fleet
from actionsplane.audit.deprecation_radar import scan_fleet
from actionsplane.audit.sarif_ingest_service import ingest_sarif
from actionsplane.audit.sarif_service import upload_sarif_for_repo
from actionsplane.audit.scorecard import build_scorecard
from actionsplane.audit.service import audit_repo
from actionsplane.config import get_settings
from actionsplane.db.base import get_session, get_sessionmaker
from actionsplane.db.repository import (
    campaign_target_counts,
    campaign_verification,
    campaign_verification_counts,
    count_open_findings,
    count_open_findings_grouped,
    create_binding,
    create_campaign,
    create_notification_channel,
    fleet_cost_records,
    get_campaign,
    get_notification_channel,
    get_repo,
    get_repo_by_owner_name,
    latest_runs_for,
    list_all_workflows,
    list_bindings,
    list_campaigns,
    list_failing_jobs,
    list_jobs,
    list_notification_channels,
    list_repos,
    list_runs,
    list_targets,
    list_templates,
    list_workflow_relations,
    list_workflows,
    list_write_audit,
    metrics_records,
    open_findings,
    record_write_audit,
    set_repo_watched,
    snapshot_campaign_findings,
    upsert_installation,
    upsert_template,
    workflow_ids_with_open_finding,
)
from actionsplane.drift.binding_service import drift_detail
from actionsplane.events import subscribe
from actionsplane.events.bus import SubscriberLimit
from actionsplane.executor.actions import rerun_run
from actionsplane.executor.campaigns import apply_campaign, run_dry_run
from actionsplane.executor.operations import OPERATION_FINDING_TYPES, OPERATIONS
from actionsplane.github.client import GitHubClient
from actionsplane.github.factory import TokenCache, app_jwt, client_for_installation
from actionsplane.governance.service import require_check_fleet
from actionsplane.metrics import summarize_runs
from actionsplane.metrics.compute import percentile
from actionsplane.metrics.cost_advice import cost_suggestions
from actionsplane.notifications import EVENT_TYPES
from actionsplane.notifications import send_test as send_test_notification
from actionsplane.notifications.service import safe_error, validate_destination
from actionsplane.observability import instrument_fastapi, setup_tracing
from actionsplane.offline import last_sync, sync_offline
from actionsplane.offline.sync import OFFLINE_INSTALLATION_ID, sync_repo
from actionsplane.policy import Policy
from actionsplane.policy.service import simulate_policy
from actionsplane.relations import build_pipeline_graph
from actionsplane.reporting import build_campaign_evidence, evidence_zip

log = logging.getLogger(__name__)


_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """On startup in offline mode, kick off an initial sync in the background (non-blocking,
    so the API serves immediately and a slow/rate-limited fetch can't delay readiness)."""
    if get_settings().offline_mode:

        async def _initial_sync() -> None:
            try:
                async with get_sessionmaker()() as session:
                    await sync_offline(session)
            except Exception:  # never let a fetch failure crash startup
                log.exception("offline initial sync failed")

        task = asyncio.create_task(_initial_sync())
        _background_tasks.add(task)  # keep a ref so the task isn't garbage-collected
        task.add_done_callback(_background_tasks.discard)
    yield


setup_tracing("actionsplane-api")
app = FastAPI(title="ActionsPlane API", version=__version__, lifespan=lifespan)
instrument_fastapi(app)

# gzip JSON responses over ~1 KiB (the run grid / findings lists compress well). Streaming
# responses (the SSE event stream) set no Content-Length, so GZipMiddleware leaves them untouched
# — no need to special-case the route, but that's why the buffering-sensitive stream is unaffected.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS only when origins are explicitly configured. Default: no middleware → same-origin only,
# which is the safe posture for a deployment that may run token-open (review 4). Credentials are
# left off deliberately — the API authenticates via a bearer token the UI attaches, not cookies.
_cors_origins = get_settings().cors_origin_list
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/repos", response_model=list[RepoOut])
async def get_repos(
    watched_only: bool = Query(True),
    session: AsyncSession = Depends(get_session),
) -> list[RepoOut]:
    repos = await list_repos(session, watched_only=watched_only)
    return [RepoOut.model_validate(r, from_attributes=True) for r in repos]


@router.post("/repos", response_model=RepoOut, status_code=201)
async def add_repo_endpoint(
    body: RepoAddIn,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> RepoOut:
    """Add a repository to the watched fleet. A previously-removed (unwatched) repo is simply
    re-watched; a brand-new one is fetched from GitHub (public read / PAT) and its runs + audit
    populated, mirroring the offline sync path."""
    existing = await get_repo_by_owner_name(session, body.owner, body.name)
    if existing is not None:
        await set_repo_watched(session, existing.id, True)
        await record_write_audit(
            session, actor=actor, action="repo.add", target=f"repo:{body.owner}/{body.name}"
        )
        await session.commit()
        repo = await get_repo(session, existing.id)
        return RepoOut.model_validate(repo, from_attributes=True)

    # New repo — fetch it (offline installation FK target must exist first).
    settings = get_settings()
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
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            gh = GitHubClient(settings.github_token, client=http)
            await sync_repo(session, gh, body.owner, body.name)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502, f"GitHub couldn't add {body.owner}/{body.name} ({exc.response.status_code})"
        ) from exc
    except Exception as exc:  # bad name, private without a token, etc.
        raise HTTPException(422, f"could not add {body.owner}/{body.name}: {exc}") from exc

    repo = await get_repo_by_owner_name(session, body.owner, body.name)
    if repo is None:
        raise HTTPException(404, "repository not found after fetch")
    await set_repo_watched(session, repo.id, True)
    await record_write_audit(
        session, actor=actor, action="repo.add", target=f"repo:{body.owner}/{body.name}"
    )
    await session.commit()
    repo = await get_repo(session, repo.id)
    return RepoOut.model_validate(repo, from_attributes=True)


@router.delete("/repos/{repo_id}")
async def remove_repo_endpoint(
    repo_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> dict[str, object]:
    """Remove a repository from the watched fleet (soft — unwatch, so history is retained and a
    webhook/reconcile sweep can't resurrect it)."""
    repo = await get_repo(session, repo_id)
    if repo is None:
        raise HTTPException(404, "repository not found")
    await set_repo_watched(session, repo_id, False)
    await record_write_audit(
        session, actor=actor, action="repo.remove", target=f"repo:{repo.owner}/{repo.name}"
    )
    await session.commit()
    return {"status": "removed", "repo_id": repo_id}


@router.get("/repos/{repo_id}/workflows", response_model=list[WorkflowOut])
async def get_workflows(
    repo_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[WorkflowOut]:
    workflows = await list_workflows(session, repo_id=repo_id)
    return [WorkflowOut.model_validate(w, from_attributes=True) for w in workflows]


@router.get("/runs", response_model=list[RunOut])
async def get_runs(
    repo_id: int | None = Query(None),
    workflow_id: int | None = Query(None),
    branch: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[RunOut]:
    runs = await list_runs(
        session,
        repo_id=repo_id,
        workflow_id=workflow_id,
        branch=branch,
        status=status,
        limit=limit,
    )
    return [RunOut.model_validate(r, from_attributes=True) for r in runs]


def _job_steps(job) -> list[dict]:
    """Extract the step list GitHub stores on the job payload (the failed-step detail)."""
    steps = (job.raw_payload or {}).get("steps") or []
    return [
        {
            "name": s.get("name"),
            "status": s.get("status"),
            "conclusion": s.get("conclusion"),
            "number": s.get("number"),
        }
        for s in steps
        if isinstance(s, dict)
    ]


@router.get("/runs/{run_id}/jobs", response_model=list[JobOut])
async def get_jobs(
    run_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[JobOut]:
    jobs = await list_jobs(session, run_id=run_id)
    return [
        JobOut.model_validate({**j.__dict__, "steps": _job_steps(j)}, from_attributes=True)
        for j in jobs
    ]


@router.get("/mode", response_model=ModeOut)
async def get_mode() -> ModeOut:
    """Tell the UI whether to show live (SSE) updates or the offline Sync button."""
    settings = get_settings()
    ls = last_sync()
    return ModeOut(
        offline=settings.offline_mode,
        live=not settings.offline_mode,
        repos=settings.offline_repo_list,
        synced_at=ls["at"],
    )


@router.post("/offline/sync", response_model=ModeOut)
async def offline_sync_endpoint(
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> ModeOut:
    """Re-pull all configured offline repos (the dashboard's Sync button)."""
    settings = get_settings()
    if not settings.offline_mode:
        raise HTTPException(409, "offline mode is not enabled (set ACTIONSPLANE_OFFLINE_REPOS)")
    ls = await sync_offline(session)
    await record_write_audit(
        session,
        actor=actor,
        action="offline.sync",
        detail={"repos": ls["repos"], "runs": ls["runs"], "findings": ls["findings"]},
    )
    await session.commit()
    return ModeOut(offline=True, live=False, repos=settings.offline_repo_list, synced_at=ls["at"])


@router.post("/runs/{run_id}/rerun")
async def rerun_run_endpoint(
    run_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> dict[str, str]:
    """Re-run a workflow run on GitHub. Needs the GitHub App configured + ``actions: write``.

    This is a write to GitHub, so it requires the operate token (a read-only token gets 403)
    and lands a row in the write-audit log.
    """
    try:
        await rerun_run(session, run_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:  # GitHub App not configured
        raise HTTPException(503, str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502, f"GitHub rejected the re-run ({exc.response.status_code})"
        ) from exc
    await record_write_audit(session, actor=actor, action="run.rerun", target=f"run:{run_id}")
    await session.commit()
    return {"status": "rerun-requested", "run_id": str(run_id)}


@router.get("/workflows/{workflow_id}/metrics", response_model=MetricsOut)
async def get_workflow_metrics(
    workflow_id: int,
    limit: int = Query(500, le=2000),
    session: AsyncSession = Depends(get_session),
) -> MetricsOut:
    records = await metrics_records(session, workflow_id=workflow_id, limit=limit)
    return MetricsOut(**asdict(summarize_runs(records)))


# Keyed TTL cache for the fleet cost rollup (review: /metrics/fleet loads every run in the window
# fleet-wide and computes percentiles in Python — expensive, and the dashboard polls it). One
# result per (days, limit) window per TTL; shares the pipelines TTL knob. The lock collapses a
# thundering herd on expiry to a single rebuild.
_fleet_cost_cache: dict[tuple[int, int], tuple[float, object]] = {}
_fleet_cost_lock = asyncio.Lock()


@router.get("/metrics/fleet", response_model=FleetCostOut)
async def fleet_cost_metrics(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> FleetCostOut:
    """Fleet cost/time rollup: per-workflow total run-minutes (the compute-cost proxy), run count,
    failures, p95 duration and queue time over the last ``days`` — ranked most-expensive first.
    Cached for a short TTL (the rollup scans the window's runs fleet-wide)."""
    ttl = get_settings().pipelines_cache_ttl_seconds
    key = (days, limit)
    if ttl > 0 and key in _fleet_cost_cache:
        at, value = _fleet_cost_cache[key]
        if time.monotonic() - at < ttl:
            return value  # type: ignore[return-value]
    async with _fleet_cost_lock:
        if ttl > 0 and key in _fleet_cost_cache:  # a concurrent request may have just rebuilt it
            at, value = _fleet_cost_cache[key]
            if time.monotonic() - at < ttl:
                return value  # type: ignore[return-value]
        result = await _build_fleet_cost(session, days, limit)
        if ttl > 0:
            _fleet_cost_cache[key] = (time.monotonic(), result)
        return result


async def _build_fleet_cost(session: AsyncSession, days: int, limit: int) -> FleetCostOut:
    since = datetime.now(UTC) - timedelta(days=days)
    records = await fleet_cost_records(session, since=since)
    workflows = {w.id: w for w in await list_all_workflows(session)}
    repos = {r.id: f"{r.owner}/{r.name}" for r in await list_repos(session, watched_only=False)}

    # Workflows with an open "missing concurrency" finding lack a concurrency: block, so the queue
    # advice can offer the one-click add-concurrency fix only where it applies. Unbounded
    # (a capped findings page would wrongly treat overflow workflows as already-concurrent).
    no_concurrency = await workflow_ids_with_open_finding(session, "missing_concurrency")

    by_wf: dict[int, list[dict]] = defaultdict(list)
    for rec in records:
        by_wf[rec["workflow_id"]].append(rec)

    # First pass: compute the rollups (need the fleet total before advice on fleet-share).
    fleet_total = 0.0
    stats: list[dict] = []
    for wid, recs in by_wf.items():
        durs = [x["duration_s"] for x in recs if x["duration_s"] is not None]
        queues = [x["queue_s"] for x in recs if x["queue_s"] is not None]
        total_s = sum(durs)
        fleet_total += total_s
        wf = workflows.get(wid)
        stats.append(
            {
                "wid": wid,
                "wf": wf,
                "name": (wf.name or wf.path.rsplit("/", 1)[-1]) if wf else str(wid),
                "runs": len(recs),
                "failures": sum(1 for x in recs if x["conclusion"] == "failure"),
                "total_s": total_s,
                "durs": durs,
                "queues": queues,
                "p95_duration_s": percentile(durs, 95),
                "p95_queue_s": percentile(queues, 95),
                "last_run_at": max(
                    (x["created_at"] for x in recs if x["created_at"]), default=None
                ),
            }
        )

    fleet_minutes = fleet_total / 60
    rows: list[FleetCostRow] = []
    for s in stats:
        total_minutes = round(s["total_s"] / 60, 1)
        advice = cost_suggestions(
            runs=s["runs"],
            failures=s["failures"],
            total_minutes=total_minutes,
            p95_duration_s=s["p95_duration_s"],
            p95_queue_s=s["p95_queue_s"],
            fleet_minutes=fleet_minutes,
            has_concurrency=s["wid"] not in no_concurrency,
        )
        wf = s["wf"]
        rows.append(
            FleetCostRow(
                workflow_id=s["wid"],
                repo=repos.get(wf.repo_id) if wf else None,
                name=s["name"],
                runs=s["runs"],
                failures=s["failures"],
                total_minutes=total_minutes,
                avg_duration_s=round(s["total_s"] / len(s["durs"]), 1) if s["durs"] else None,
                p95_duration_s=s["p95_duration_s"],
                p95_queue_s=s["p95_queue_s"],
                last_run_at=s["last_run_at"],
                suggestions=[
                    CostSuggestionOut(
                        kind=a.kind,
                        severity=a.severity,
                        message=a.message,
                        fix_operation=a.fix_operation,
                    )
                    for a in advice
                ],
            )
        )
    rows.sort(key=lambda r: r.total_minutes, reverse=True)
    return FleetCostOut(window_days=days, total_minutes=round(fleet_minutes, 1), rows=rows[:limit])


@router.get("/findings", response_model=FindingsPage)
async def get_findings(
    repo_id: int | None = Query(None),
    severity: str | None = Query(None),
    finding_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    # Bound the offset too: an unbounded deep offset walks the whole index (review 4, NEW-6).
    offset: int = Query(0, ge=0, le=1_000_000),
    session: AsyncSession = Depends(get_session),
) -> FindingsPage:
    """A page of open findings plus the unpaginated total, so the UI never silently truncates."""
    filters = {"repo_id": repo_id, "severity": severity, "finding_type": finding_type}
    items = await open_findings(session, **filters, limit=limit, offset=offset)
    total = await count_open_findings(session, **filters)
    return FindingsPage(
        items=[FindingOut.model_validate(f, from_attributes=True) for f in items], total=total
    )


@router.post("/repos/{repo_id}/sarif/upload")
async def upload_repo_sarif_endpoint(
    repo_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> dict[str, str]:
    """Push this repo's open findings to GitHub Code Scanning (the find→fix bridge).

    Requires the GitHub App + ``security_events: write`` and ``security_events_enabled=true``.
    Operate token only (read token → 403); audited.
    """
    try:
        result = await upload_sarif_for_repo(session, repo_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:  # GitHub App not configured
        raise HTTPException(503, str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502, f"GitHub rejected the SARIF upload ({exc.response.status_code})"
        ) from exc
    await record_write_audit(
        session,
        actor=actor,
        action="sarif.upload",
        target=f"repo:{repo_id}",
        detail={"analysis_url": str(result.get("url", ""))},
    )
    await session.commit()
    return {"status": "sarif-uploaded", "analysis_url": str(result.get("url", ""))}


@router.post("/repos/{repo_id}/sarif/ingest", response_model=SarifIngestOut)
async def ingest_repo_sarif_endpoint(
    repo_id: int,
    body: SarifIngestIn,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> SarifIngestOut:
    """Ingest an external scanner's SARIF (zizmor/poutine/Scorecard) as findings (D1).

    Operate token only (read token → 403); audited. Lifecycle is per-tool, so this never disturbs
    ActionsPlane's native findings or another scanner's.
    """
    result = await ingest_sarif(session, repo_id=repo_id, doc=body.sarif)
    await record_write_audit(
        session,
        actor=actor,
        action="sarif.ingest",
        target=f"repo:{repo_id}",
        detail=result,
    )
    await session.commit()
    return SarifIngestOut(**result)


@router.get("/repos/{repo_id}/findings", response_model=list[FindingOut])
async def get_repo_findings(
    repo_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[FindingOut]:
    findings = await open_findings(session, repo_id=repo_id)
    return [FindingOut.model_validate(f, from_attributes=True) for f in findings]


async def _failing_steps_for(
    session: AsyncSession, run_ids: list[int]
) -> dict[int, tuple[str | None, str | None]]:
    """Batched (job, step) failure detail for many runs at once — one query, not one per run.

    For each run: the first failing step in the first failing job (jobs ordered by id, mirroring
    their lifecycle order). Returns ``{run_id: (job_name, step_name)}``; a run with no failing job
    is simply absent. Replaces the per-node ``_failing_step`` that made ``/pipelines`` N+1."""
    out: dict[int, tuple[str | None, str | None]] = {}
    for job in await list_failing_jobs(session, run_ids):
        if job.run_id in out:
            continue  # first failing job per run wins (query is ordered by run_id, id)
        step_name = next(
            (s.get("name") for s in _job_steps(job) if s.get("conclusion") == "failure"), None
        )
        out[job.run_id] = (job.name, step_name)
    return out


# Single-flight TTL cache for the fleet pipeline graph (review §5 M5). One rebuild per TTL window,
# shared across concurrent viewers; the lock collapses a thundering herd on expiry to one rebuild.
_pipelines_cache: dict[str, object] = {"at": 0.0, "value": None}
_pipelines_lock = asyncio.Lock()


@router.get("/pipelines", response_model=PipelineGraphOut)
async def get_pipelines(session: AsyncSession = Depends(get_session)) -> PipelineGraphOut:
    """The fleet-wide cross-workflow trigger/dependency graph, each node annotated with its
    latest-run status (and, when failed, the job/step that failed). Cached for a short TTL."""
    ttl = get_settings().pipelines_cache_ttl_seconds
    now = time.monotonic()
    cached = _pipelines_cache["value"]
    if ttl > 0 and cached is not None and now - float(_pipelines_cache["at"]) < ttl:
        return cached  # type: ignore[return-value]
    async with _pipelines_lock:
        # Re-check inside the lock: a concurrent request may have just rebuilt it.
        now = time.monotonic()
        cached = _pipelines_cache["value"]
        if ttl > 0 and cached is not None and now - float(_pipelines_cache["at"]) < ttl:
            return cached  # type: ignore[return-value]
        graph = await _build_pipelines(session)
        if ttl > 0:
            _pipelines_cache["value"] = graph
            _pipelines_cache["at"] = time.monotonic()
        return graph


async def _build_pipelines(session: AsyncSession) -> PipelineGraphOut:
    relations = await list_workflow_relations(session)
    repos = {r.id: r for r in await list_repos(session, watched_only=False)}

    # Map (repo_id, path) → workflow → latest run, so each relation node carries live status.
    wf_by_key = {(w.repo_id, w.path): w for w in await list_all_workflows(session)}
    wf_ids = [
        wf_by_key[(rel.repo_id, rel.path)].id
        for rel in relations
        if (rel.repo_id, rel.path) in wf_by_key
    ]
    latest = await latest_runs_for(session, wf_ids)
    # One batched query for the failing (job, step) of every failed latest-run, instead of a
    # per-node round-trip (the old N+1). Keyed by run id.
    failing = await _failing_steps_for(
        session, [run.id for run in latest.values() if run.conclusion == "failure"]
    )

    items = []
    for rel in relations:
        if rel.repo_id not in repos:
            continue
        status = None
        wf = wf_by_key.get((rel.repo_id, rel.path))
        run = latest.get(wf.id) if wf else None
        if run is not None:
            failed_job = failed_step = None
            if run.conclusion == "failure":
                failed_job, failed_step = failing.get(run.id, (None, None))
            status = {
                "status": run.status,
                "conclusion": run.conclusion,
                "run_id": run.id,
                "run_number": run.run_number,
                "failed_job": failed_job,
                "failed_step": failed_step,
            }
        items.append(
            {
                "repo": f"{repos[rel.repo_id].owner}/{repos[rel.repo_id].name}",
                "path": rel.path,
                "descriptor": rel.descriptor,
                "status": status,
            }
        )
    return PipelineGraphOut(**build_pipeline_graph(items))


@router.get("/audit/scorecard", response_model=ScorecardOut)
async def get_scorecard(session: AsyncSession = Depends(get_session)) -> ScorecardOut:
    counts = await count_open_findings_grouped(session)
    repos = await list_repos(session, watched_only=True)
    sc = build_scorecard(counts, repos=len(repos))
    return ScorecardOut(**asdict(sc))


@router.post("/policy/simulate", response_model=SimulationReportOut)
async def simulate_policy_endpoint(
    body: PolicySimulateIn,
    session: AsyncSession = Depends(get_session),
) -> SimulationReportOut:
    """Simulate a proposed policy against the fleet (W2). Read-only — no writes, no GitHub I/O.

    Returns how many workflows/repos would break, a per-rule rollup with the fix operation, and
    the repo ids a fix campaign would target.
    """
    policy = Policy(
        require_sha_pinned=body.require_sha_pinned,
        disallowed_triggers=tuple(body.disallowed_triggers),
        require_permissions=body.require_permissions,
    )
    report = await simulate_policy(session, policy)
    return SimulationReportOut(**asdict(report))


@router.post("/governance/require-check", response_model=RequireCheckOut)
async def require_check_endpoint(
    body: RequireCheckIn,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> RequireCheckOut:
    """Require a status check on each listed repo's default branch (W7 — optional, gated write).

    A direct GitHub write (branch ruleset), not a PR — so it's fail-closed behind bulk-edits + the
    operate token, and needs the App's ``administration: write`` permission. Per-repo failures
    (e.g. permission not granted) are returned in the result, not raised.
    """
    try:
        results = await require_check_fleet(session, check=body.check, repo_ids=body.repo_ids)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    await record_write_audit(
        session,
        actor=actor,
        action="governance.require_check",
        target=f"check:{body.check}",
        detail={"repo_ids": body.repo_ids, "results": [r["status"] for r in results]},
    )
    await session.commit()
    return RequireCheckOut(check=body.check, results=results)


@router.get("/deprecations/radar", response_model=RadarReportOut)
async def deprecation_radar_endpoint(
    session: AsyncSession = Depends(get_session),
) -> RadarReportOut:
    """Deprecation Radar (W8): fleet impact inventory for GitHub's dated deprecations, sorted
    overdue/urgent first. Read-only — matches the curated feed against stored workflow facts."""
    report = await scan_fleet(session, as_of=date.today())
    return RadarReportOut(**asdict(report))


@router.get("/advisories/watch", response_model=AdvisoryReportOut)
async def advisory_watch_endpoint(
    session: AsyncSession = Depends(get_session),
) -> AdvisoryReportOut:
    """Advisory watch (roadmap #7): which GHSA security advisories name an action the fleet uses,
    most-severe first. Read-only — matches the polled advisory inventory against stored workflow
    facts. The inventory is refreshed by the daily ``advisory_watch_sweep`` (empty until it runs).
    """
    report = await advisory_scan_fleet(session, as_of=datetime.now(UTC))
    return AdvisoryReportOut(**asdict(report))


@router.get("/templates", response_model=list[TemplateOut])
async def get_templates(session: AsyncSession = Depends(get_session)) -> list[TemplateOut]:
    templates = await list_templates(session)
    return [TemplateOut.model_validate(t, from_attributes=True) for t in templates]


@router.get("/drift", response_model=list[BindingOut])
async def get_drift(
    repo_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[BindingOut]:
    bindings = await list_bindings(session, repo_id=repo_id)
    return [BindingOut.model_validate(b, from_attributes=True) for b in bindings]


@router.get("/drift/{binding_id}/detail", response_model=DriftDetailOut)
async def get_drift_detail(
    binding_id: int,
    session: AsyncSession = Depends(get_session),
) -> DriftDetailOut:
    """Recompute and return what drifted for one binding (change list + both YAMLs)."""
    try:
        detail = await drift_detail(session, binding_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:  # GitHub App not configured
        raise HTTPException(503, str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502, f"GitHub error fetching the workflow ({exc.response.status_code})"
        ) from exc
    return DriftDetailOut(**detail)


@router.post("/templates", response_model=TemplateOut, status_code=201)
async def create_template(
    body: TemplateCreate,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> TemplateOut:
    tpl = await upsert_template(session, name=body.name, canonical_yaml=body.canonical_yaml)
    await record_write_audit(
        session,
        actor=actor,
        action="template.create",
        target=f"template:{tpl.name}",
        detail={"version": tpl.version},
    )
    await session.commit()
    return TemplateOut.model_validate(tpl, from_attributes=True)


@router.post("/repos/{repo_id}/bindings", response_model=BindingOut, status_code=201)
async def add_binding(
    repo_id: int,
    body: BindingCreate,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> BindingOut:
    binding = await create_binding(
        session, repo_id=repo_id, template_id=body.template_id, path=body.path
    )
    await record_write_audit(
        session,
        actor=actor,
        action="binding.create",
        target=f"repo:{repo_id}",
        detail={"template_id": body.template_id, "path": body.path},
    )
    await session.commit()
    return BindingOut.model_validate(binding, from_attributes=True)


async def _campaign_out(session: AsyncSession, campaign) -> CampaignOut:
    targets = await list_targets(session, campaign_id=campaign.id)
    verification = await _campaign_verification_out(session, campaign.id)
    return CampaignOut(
        id=campaign.id,
        name=campaign.name,
        operation=campaign.operation,
        status=campaign.status,
        created_at=campaign.created_at,
        targets=[CampaignTargetOut.model_validate(t, from_attributes=True) for t in targets],
        verification=verification,
    )


async def _campaign_verification_out(
    session: AsyncSession, campaign_id: int
) -> CampaignVerification | None:
    """Build the verified-closed rollup for a campaign, resolving repo names. ``None`` when the
    campaign's operation isn't finding-backed (nothing was snapshotted)."""
    v = await campaign_verification(session, campaign_id)
    if v["targeted"] == 0:
        return None
    repos = {r.id: f"{r.owner}/{r.name}" for r in await list_repos(session, watched_only=False)}
    return CampaignVerification(
        targeted=v["targeted"],
        resolved=v["resolved"],
        by_repo=[
            CampaignRepoProgress(
                repo_id=p["repo_id"],
                repo=repos.get(p["repo_id"]),
                targeted=p["targeted"],
                resolved=p["resolved"],
            )
            for p in v["by_repo"]
        ],
        by_type=v["by_type"],
    )


@router.get("/campaigns", response_model=list[CampaignSummaryOut])
async def list_campaigns_endpoint(
    limit: int = Query(100, le=500, ge=1),
    session: AsyncSession = Depends(get_session),
) -> list[CampaignSummaryOut]:
    """All campaigns newest-first, with per-campaign target counts (for the Campaigns tab list)."""
    campaigns = await list_campaigns(session, limit=limit)
    ids = [c.id for c in campaigns]
    counts = await campaign_target_counts(session, ids)
    verified = await campaign_verification_counts(session, ids)
    out: list[CampaignSummaryOut] = []
    for c in campaigns:
        total, opened, errored = counts.get(c.id, (0, 0, 0))
        targeted, resolved = verified.get(c.id, (0, 0))
        out.append(
            CampaignSummaryOut(
                id=c.id,
                name=c.name,
                operation=c.operation,
                status=c.status,
                created_at=c.created_at,
                targets=total,
                opened=opened,
                errored=errored,
                targeted_findings=targeted,
                resolved_findings=resolved,
            )
        )
    return out


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
async def create_campaign_endpoint(
    body: CampaignCreate,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> CampaignOut:
    """Create a bulk-edit campaign and immediately compute its dry-run diffs (no writes)."""
    if body.operation not in OPERATIONS:
        raise HTTPException(422, f"unknown operation {body.operation!r}")
    campaign = await create_campaign(
        session,
        name=body.name,
        operation=body.operation,
        params=body.params,
        repo_ids=body.repo_ids,
        created_by="api",
    )
    # Verified-closed snapshot: record the open findings this operation is meant to close, so we can
    # later prove how many the merged PRs actually resolved (not just that PRs opened).
    await snapshot_campaign_findings(
        session,
        campaign_id=campaign.id,
        repo_ids=body.repo_ids,
        finding_types=set(OPERATION_FINDING_TYPES.get(body.operation, frozenset())),
    )
    await record_write_audit(
        session,
        actor=actor,
        action="campaign.create",
        target=f"campaign:{campaign.id}",
        detail={
            "name": body.name,
            "operation": body.operation,
            "repo_ids": body.repo_ids,
            "params": body.params,
        },
    )
    await session.commit()
    await run_dry_run(session, campaign)
    return await _campaign_out(session, campaign)


@router.post("/campaigns/{campaign_id}/apply", response_model=CampaignOut)
async def apply_campaign_endpoint(
    campaign_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> CampaignOut:
    """Open PRs for the campaign. Requires bulk edits enabled (human-triggered)."""
    campaign = await get_campaign(session, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    try:
        await apply_campaign(session, campaign)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    targets = await list_targets(session, campaign_id=campaign.id)
    await record_write_audit(
        session,
        actor=actor,
        action="campaign.apply",
        target=f"campaign:{campaign.id}",
        detail={"pr_urls": [t.pr_url for t in targets if t.pr_url]},
    )
    await session.commit()
    return await _campaign_out(session, campaign)


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign_endpoint(
    campaign_id: int,
    session: AsyncSession = Depends(get_session),
) -> CampaignOut:
    campaign = await get_campaign(session, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    return await _campaign_out(session, campaign)


@router.get("/campaigns/{campaign_id}/export")
async def export_campaign_evidence_endpoint(
    campaign_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> Response:
    """Download a compliance evidence bundle (zip) for a campaign.

    The zip holds a printable ``report.html``, a machine-readable ``evidence.json``, and a SHA-256
    ``manifest.txt`` — proof of what the campaign targeted and what a re-audit confirms it resolved.
    Operator-gated and audit-logged: pulling evidence is itself a recorded compliance action."""
    campaign = await get_campaign(session, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    bundle = await build_campaign_evidence(session, campaign, generated_at=datetime.now(UTC))
    data = evidence_zip(bundle)
    await record_write_audit(
        session,
        actor=actor,
        action="campaign.export",
        target=f"campaign:{campaign.id}",
        detail={"targeted": bundle.targeted, "resolved": bundle.resolved, "bytes": len(data)},
    )
    await session.commit()
    filename = f"campaign-{campaign.id}-evidence.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/campaigns/{campaign_id}/verify", response_model=CampaignOut)
async def verify_campaign_endpoint(
    campaign_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> CampaignOut:
    """Re-audit the campaign's target repos and return the refreshed verified-closed rollup.

    This is the "did the fix actually land?" action: after the campaign's PRs merge, re-auditing the
    repos re-resolves the findings that are now gone, and the snapshot→findings join reflects it as
    "resolved N/M". Operator-gated (it re-audits real repos via the GitHub App)."""
    campaign = await get_campaign(session, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")

    targets = await list_targets(session, campaign_id=campaign.id)
    audited, errors = 0, []
    try:
        jwt = app_jwt()
    except RuntimeError as exc:  # GitHub App not configured — needs it (match sibling 503s)
        raise HTTPException(503, str(exc)) from exc
    # Each target gets its OWN session (mirrors the worker's audit sweep): audit_repo writes then
    # commits, so a DB error on one repo would otherwise poison the shared request transaction and
    # abort every later target + the audit-log write. A fresh session per repo also means the pooled
    # connection isn't held across this repo's GitHub I/O.
    maker = get_sessionmaker()
    async with httpx.AsyncClient(timeout=30) as http:
        cache: TokenCache = {}
        for target in targets:
            try:
                async with maker() as s:
                    repo = await get_repo(s, target.repo_id)
                    if repo is None:
                        continue
                    gh = await client_for_installation(
                        repo.installation_id, http=http, jwt=jwt, token_cache=cache
                    )
                    await audit_repo(s, gh, repo)
                audited += 1
            except Exception as exc:  # one repo failing shouldn't abort the verify sweep
                errors.append(f"repo {target.repo_id}: {str(exc)[:200]}")
    await record_write_audit(
        session,
        actor=actor,
        action="campaign.verify",
        target=f"campaign:{campaign.id}",
        detail={"repos_audited": audited, "errors": errors},
    )
    await session.commit()
    return await _campaign_out(session, campaign)


def _mask_destination(kind: str, config: dict) -> str | None:
    """A human hint at a channel's destination without leaking the secret (URL / recipient)."""
    if kind in ("slack", "webhook"):
        url = str((config or {}).get("url", ""))
        if not url:
            return None
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        host = url.split("//", 1)[-1].split("/", 1)[0]
        return f"{host}/…/{tail[-6:]}" if tail else host
    if kind == "email":
        to = str((config or {}).get("to", ""))
        if "@" not in to:
            return to or None
        local, _, domain = to.partition("@")
        return f"{local[:1]}***@{domain}"
    return None


def _channel_out(channel) -> NotificationChannelOut:
    return NotificationChannelOut(
        id=channel.id,
        kind=channel.kind,
        name=channel.name,
        events=channel.events or [],
        enabled=channel.enabled,
        created_at=channel.created_at,
        target_hint=_mask_destination(channel.kind, channel.config or {}),
    )


@router.get("/meta/openapi")
async def meta_openapi(request: Request) -> dict:
    """The OpenAPI schema, so the in-product Docs tab can render an API reference from the live app
    (always in sync with the deployed endpoints — no separate doc to drift)."""
    return request.app.openapi()


@router.get("/notifications/events", response_model=list[NotificationEventOut])
async def list_notification_events() -> list[NotificationEventOut]:
    """The event types a channel can subscribe to (for the settings UI)."""
    return [
        NotificationEventOut(type=t, label=meta["label"], description=meta["description"])
        for t, meta in EVENT_TYPES.items()
    ]


@router.get("/notifications/channels", response_model=list[NotificationChannelOut])
async def list_channels_endpoint(
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> list[NotificationChannelOut]:
    """Configured alert channels (destinations masked). Operator-gated — reveals routing config."""
    return [_channel_out(c) for c in await list_notification_channels(session)]


@router.post("/notifications/channels", response_model=NotificationChannelOut, status_code=201)
async def create_channel_endpoint(
    body: NotificationChannelIn,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> NotificationChannelOut:
    unknown = [e for e in body.events if e not in EVENT_TYPES]
    if unknown:
        raise HTTPException(422, f"unknown event type(s): {', '.join(unknown)}")
    try:
        validate_destination(body.kind, body.config)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    channel = await create_notification_channel(
        session, kind=body.kind, name=body.name, config=body.config, events=body.events
    )
    await record_write_audit(
        session, actor=actor, action="notification.create", target=f"channel:{channel.id}"
    )
    await session.commit()
    return _channel_out(channel)


@router.patch("/notifications/channels/{channel_id}", response_model=NotificationChannelOut)
async def update_channel_endpoint(
    channel_id: int,
    body: NotificationChannelUpdate,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> NotificationChannelOut:
    channel = await get_notification_channel(session, channel_id)
    if channel is None:
        raise HTTPException(404, "channel not found")
    if body.events is not None:
        unknown = [e for e in body.events if e not in EVENT_TYPES]
        if unknown:
            raise HTTPException(422, f"unknown event type(s): {', '.join(unknown)}")
        channel.events = body.events
    if body.name is not None:
        channel.name = body.name.strip() or channel.name
    if body.config is not None:
        try:
            validate_destination(channel.kind, body.config)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        channel.config = body.config
    if body.enabled is not None:
        channel.enabled = body.enabled
    await record_write_audit(
        session, actor=actor, action="notification.update", target=f"channel:{channel.id}"
    )
    await session.commit()
    return _channel_out(channel)


@router.delete("/notifications/channels/{channel_id}")
async def delete_channel_endpoint(
    channel_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> dict:
    channel = await get_notification_channel(session, channel_id)
    if channel is None:
        raise HTTPException(404, "channel not found")
    await session.delete(channel)
    await record_write_audit(
        session, actor=actor, action="notification.delete", target=f"channel:{channel_id}"
    )
    await session.commit()
    return {"status": "deleted", "channel_id": channel_id}


@router.post("/notifications/channels/{channel_id}/test", response_model=NotificationTestOut)
async def test_channel_endpoint(
    channel_id: int,
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> NotificationTestOut:
    """Send a test alert to one channel now, surfacing any delivery error."""
    channel = await get_notification_channel(session, channel_id)
    if channel is None:
        raise HTTPException(404, "channel not found")
    try:
        await send_test_notification(channel)
        return NotificationTestOut(status="sent")
    except Exception as exc:  # surface the failure — scrubbed so a secret URL can't leak
        return NotificationTestOut(status="error", detail=safe_error(exc))


@router.get("/audit-log", response_model=list[AuditLogEntryOut])
async def get_audit_log(
    limit: int = Query(100, le=500, ge=1),
    offset: int = Query(0, ge=0, le=1_000_000),
    session: AsyncSession = Depends(get_session),
    actor: str = Depends(require_configured_operate),
) -> list[AuditLogEntryOut]:
    """The write-operation audit trail, newest first (Phase 5.2). Gated like a write
    (``require_configured_operate``): the trail names targets and PR URLs, so it's operator-level
    information and must not be world-readable in tokenless open mode (review 4, NEW-11)."""
    rows = await list_write_audit(session, limit=limit, offset=offset)
    return [AuditLogEntryOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/events/stream")
async def events_stream(request: Request) -> EventSourceResponse:
    """Server-Sent Events: live run/job updates for the dashboard (plan §5.1).

    Disconnect-safe: the bus subscription is explicitly ``aclose()``'d in a ``finally`` so a
    closed browser tab can't strand a Redis connection. ``ping`` makes sse-starlette emit a
    keep-alive comment on idle channels — that send is what trips the disconnect detection (and
    cancels this generator) when the client is gone but no event has arrived to notice it.
    """

    async def event_generator():
        stream = subscribe()
        try:
            async for envelope in stream:
                if await request.is_disconnected():
                    break
                yield {"event": "update", "data": envelope}
        except SubscriberLimit:
            # Cap reached (the generator raises on first pull) — refuse this stream cleanly.
            log.warning("SSE subscriber cap reached; refusing new stream")
        finally:
            await stream.aclose()

    return EventSourceResponse(event_generator(), ping=15)


app.include_router(router)
