"""REST API (plan §4, Phase 1).

Serves the read model the React UI and CLI consume: repos, workflows, runs, and per-workflow
metrics. GraphQL (for efficient cross-repo queries) is layered on in a later step; REST covers
the resource reads now. Endpoints are thin: repository query -> response model.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from actionsplane import __version__
from actionsplane.api.auth import require_token
from actionsplane.api.schemas import (
    BindingCreate,
    BindingOut,
    CampaignCreate,
    CampaignOut,
    CampaignTargetOut,
    FindingOut,
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
from actionsplane.db.models import WorkflowRun
from actionsplane.db.repository import (
    create_binding,
    create_campaign,
    get_campaign,
    latest_runs_for,
    list_all_workflows,
    list_bindings,
    list_jobs,
    list_repos,
    list_runs,
    list_targets,
    list_templates,
    list_workflow_relations,
    list_workflows,
    open_findings,
    upsert_template,
)
from actionsplane.events import subscribe
from actionsplane.executor.actions import rerun_run
from actionsplane.executor.campaigns import apply_campaign, run_dry_run
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
async def offline_sync_endpoint(session: AsyncSession = Depends(get_session)) -> ModeOut:
    """Re-pull all configured offline repos (the dashboard's Sync button)."""
    settings = get_settings()
    if not settings.offline_mode:
        raise HTTPException(409, "offline mode is not enabled (set ACTIONSPLANE_OFFLINE_REPOS)")
    ls = await sync_offline(session)
    return ModeOut(offline=True, live=False, repos=settings.offline_repo_list, synced_at=ls["at"])


@router.post("/runs/{run_id}/rerun")
async def rerun_run_endpoint(
    run_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Re-run a workflow run on GitHub. Needs the GitHub App configured + ``actions: write``.

    This is a write to GitHub, so it sits behind the same ``/api/v1`` bearer-token gate as the
    rest of the mutating endpoints (enforced when ``ACTIONSPLANE_API_TOKEN`` is set).
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
    return {"status": "rerun-requested", "run_id": str(run_id)}


@router.get("/workflows/{workflow_id}/metrics", response_model=MetricsOut)
async def get_workflow_metrics(
    workflow_id: int,
    limit: int = Query(500, le=2000),
    session: AsyncSession = Depends(get_session),
) -> MetricsOut:
    runs = await list_runs(session, workflow_id=workflow_id, limit=limit)
    records = [run_to_record(r) for r in runs]
    return MetricsOut(**asdict(summarize_runs(records)))


def run_to_record(run: WorkflowRun) -> dict:
    """Derive duration/queue seconds from a run row for the metrics functions."""
    duration_s = queue_s = None
    if run.started_at and run.completed_at:
        duration_s = (run.completed_at - run.started_at).total_seconds()
    if run.created_at and run.started_at:
        queue_s = (run.started_at - run.created_at).total_seconds()
    return {
        "conclusion": run.conclusion,
        "head_sha": run.head_sha,
        "duration_s": duration_s,
        "queue_s": queue_s,
    }


@router.get("/findings", response_model=list[FindingOut])
async def get_findings(
    repo_id: int | None = Query(None),
    severity: str | None = Query(None),
    finding_type: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[FindingOut]:
    findings = await open_findings(
        session, repo_id=repo_id, severity=severity, finding_type=finding_type
    )
    return [FindingOut.model_validate(f, from_attributes=True) for f in findings]


@router.post("/repos/{repo_id}/sarif/upload")
async def upload_repo_sarif_endpoint(
    repo_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Push this repo's open findings to GitHub Code Scanning (the find→fix bridge).

    Requires the GitHub App + ``security_events: write`` and ``security_events_enabled=true``.
    Behind the ``/api/v1`` bearer-token gate like the other writes.
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
    return {"status": "sarif-uploaded", "analysis_url": str(result.get("url", ""))}


@router.get("/repos/{repo_id}/findings", response_model=list[FindingOut])
async def get_repo_findings(
    repo_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[FindingOut]:
    findings = await open_findings(session, repo_id=repo_id)
    return [FindingOut.model_validate(f, from_attributes=True) for f in findings]


async def _failing_step(session: AsyncSession, run_id: int) -> tuple[str | None, str | None]:
    """For a failed run, find the (job, step) that failed — the first failing step in the first
    failing job. Returns (job_name, step_name); either may be None if steps weren't recorded."""
    for job in await list_jobs(session, run_id=run_id):
        if job.conclusion == "failure":
            for step in _job_steps(job):
                if step.get("conclusion") == "failure":
                    return job.name, step.get("name")
            return job.name, None
    return None, None


@router.get("/pipelines", response_model=PipelineGraphOut)
async def get_pipelines(session: AsyncSession = Depends(get_session)) -> PipelineGraphOut:
    """The fleet-wide cross-workflow trigger/dependency graph, each node annotated with the
    status of its latest run (and, when failed, the job/step that failed)."""
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
                failed_job, failed_step = await _failing_step(session, run.id)
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
    findings = await open_findings(session)
    repos = await list_repos(session, watched_only=True)
    sc = build_scorecard(findings, repos=len(repos))
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
) -> TemplateOut:
    tpl = await upsert_template(session, name=body.name, canonical_yaml=body.canonical_yaml)
    await session.commit()
    return TemplateOut.model_validate(tpl, from_attributes=True)


@router.post("/repos/{repo_id}/bindings", response_model=BindingOut, status_code=201)
async def add_binding(
    repo_id: int,
    body: BindingCreate,
    session: AsyncSession = Depends(get_session),
) -> BindingOut:
    binding = await create_binding(
        session, repo_id=repo_id, template_id=body.template_id, path=body.path
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
) -> CampaignOut:
    """Create a bulk-edit campaign and immediately compute its dry-run diffs (no writes)."""
    campaign = await create_campaign(
        session,
        name=body.name,
        operation=body.operation,
        params=None,
        repo_ids=body.repo_ids,
        created_by="api",
    )
    await session.commit()
    await run_dry_run(session, campaign)
    return await _campaign_out(session, campaign)


@router.post("/campaigns/{campaign_id}/apply", response_model=CampaignOut)
async def apply_campaign_endpoint(
    campaign_id: int,
    session: AsyncSession = Depends(get_session),
) -> CampaignOut:
    """Open PRs for the campaign. Requires bulk edits enabled (human-triggered)."""
    campaign = await get_campaign(session, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    try:
        await apply_campaign(session, campaign)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
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
        finally:
            await stream.aclose()

    return EventSourceResponse(event_generator(), ping=15)


app.include_router(router)
