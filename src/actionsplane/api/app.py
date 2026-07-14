"""REST API (plan §4, Phase 1).

Serves the read model the React UI and CLI consume: repos, workflows, runs, and per-workflow
metrics. GraphQL (for efficient cross-repo queries) is layered on in a later step; REST covers
the resource reads now. Endpoints are thin: repository query -> response model.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from actionsplane import __version__
from actionsplane.api.auth import require_configured_operate, require_token
from actionsplane.api.schemas import (
    AuditLogEntryOut,
    BindingCreate,
    BindingOut,
    CampaignCreate,
    CampaignOut,
    CampaignTargetOut,
    FindingOut,
    FindingsPage,
    JobOut,
    MetricsOut,
    ModeOut,
    PipelineGraphOut,
    RepoOut,
    RunOut,
    ScorecardOut,
    TemplateCreate,
    TemplateOut,
    WorkflowOut,
)
from actionsplane.audit.sarif_service import upload_sarif_for_repo
from actionsplane.audit.scorecard import build_scorecard
from actionsplane.config import get_settings
from actionsplane.db.base import get_session, get_sessionmaker
from actionsplane.db.repository import (
    count_open_findings,
    count_open_findings_grouped,
    create_binding,
    create_campaign,
    get_campaign,
    latest_runs_for,
    list_all_workflows,
    list_bindings,
    list_failing_jobs,
    list_jobs,
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
    upsert_template,
)
from actionsplane.events import subscribe
from actionsplane.events.bus import SubscriberLimit
from actionsplane.executor.actions import rerun_run
from actionsplane.executor.campaigns import apply_campaign, run_dry_run
from actionsplane.executor.operations import OPERATIONS
from actionsplane.metrics import summarize_runs
from actionsplane.observability import instrument_fastapi, setup_tracing
from actionsplane.offline import last_sync, sync_offline
from actionsplane.relations import build_pipeline_graph

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
    return CampaignOut(
        id=campaign.id,
        name=campaign.name,
        operation=campaign.operation,
        status=campaign.status,
        targets=[CampaignTargetOut.model_validate(t, from_attributes=True) for t in targets],
    )


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
        params=None,
        repo_ids=body.repo_ids,
        created_by="api",
    )
    await record_write_audit(
        session,
        actor=actor,
        action="campaign.create",
        target=f"campaign:{campaign.id}",
        detail={"name": body.name, "operation": body.operation, "repo_ids": body.repo_ids},
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
