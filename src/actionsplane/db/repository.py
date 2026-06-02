"""Repository layer — async upsert + query helpers (plan §7, Phase 1).

Thin functions over the ORM so the ingestor/worker/API don't hand-roll SQL. Upserts use the
Postgres ``ON CONFLICT`` so replayed webhook deliveries (at-least-once) are idempotent — the
same run id simply updates the existing row. Queries return ORM instances; the API layer maps
them to response models.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.db.models import (
    AuditFinding,
    Campaign,
    CampaignTarget,
    Installation,
    ProcessedDelivery,
    Repo,
    TemplateBinding,
    Workflow,
    WorkflowJob,
    WorkflowRelation,
    WorkflowRun,
    WorkflowTemplate,
)


def _conflict_insert(session: AsyncSession):
    """Return the dialect-appropriate INSERT construct factory.

    ``ON CONFLICT ... DO UPDATE`` is dialect-specific. Postgres in production; sqlite for
    hermetic tests of the upsert paths. Both support ``DO UPDATE ... WHERE``, which the
    conditional run upsert relies on.
    """
    return sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert


async def _upsert(
    session: AsyncSession, model: type, values: dict[str, Any], pk: str = "id"
) -> None:
    """Insert ``values`` or, on PK conflict, update the non-PK columns."""
    stmt = _conflict_insert(session)(model).values(**values)
    update_cols = {c: stmt.excluded[c] for c in values if c != pk}
    stmt = stmt.on_conflict_do_update(index_elements=[pk], set_=update_cols)
    await session.execute(stmt)


async def upsert_repo(
    session: AsyncSession, values: dict[str, Any], *, installation_id: int
) -> None:
    await _upsert(session, Repo, {**values, "installation_id": installation_id})


async def upsert_run(session: AsyncSession, values: dict[str, Any]) -> None:
    """Upsert a run, but never let a stale event overwrite a fresher row.

    GitHub delivers ``workflow_run`` events at-least-once and out of order, so a late
    ``in_progress`` redelivery can arrive *after* the ``completed`` event for the same run id.
    An unconditional upsert would regress the row from completed back to in-progress. The run's
    ``updated_at`` (monotonic across GitHub state transitions) gates the update: it only applies
    when the incoming event is not older than the stored row — or when the stored row predates
    this column (legacy rows, ``updated_at IS NULL``). The guard stays in SQL so the check and
    the write are atomic; doing it as read-then-write would reopen the race under concurrency.
    """
    stmt = _conflict_insert(session)(WorkflowRun).values(**values)
    update_cols = {c: stmt.excluded[c] for c in values if c != "id"}
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_=update_cols,
        where=or_(
            WorkflowRun.updated_at.is_(None),
            WorkflowRun.updated_at <= stmt.excluded["updated_at"],
        ),
    )
    await session.execute(stmt)


async def upsert_installation(session: AsyncSession, values: dict[str, Any]) -> None:
    await _upsert(session, Installation, values)


async def upsert_job(session: AsyncSession, values: dict[str, Any]) -> None:
    await _upsert(session, WorkflowJob, values)


async def list_jobs(session: AsyncSession, *, run_id: int) -> list[WorkflowJob]:
    stmt = select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    return list((await session.scalars(stmt)).all())


async def list_all_workflows(session: AsyncSession) -> list[Workflow]:
    """Every workflow row across all repos (used to map (repo_id, path) → workflow id)."""
    return list((await session.scalars(select(Workflow))).all())


async def latest_runs_for(session: AsyncSession, workflow_ids: list[int]) -> dict[int, WorkflowRun]:
    """The most recent run per workflow id (by ``created_at``), for the given workflow ids.

    Scoped to the supplied ids (the Pipelines graph passes just its workflows) so this stays cheap
    even on a large run history. Returns ``{workflow_id: WorkflowRun}``.
    """
    if not workflow_ids:
        return {}
    stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.workflow_id.in_(workflow_ids))
        .order_by(WorkflowRun.created_at.desc())
    )
    out: dict[int, WorkflowRun] = {}
    for run in (await session.scalars(stmt)).all():
        out.setdefault(run.workflow_id, run)  # first seen = newest (desc order)
    return out


async def get_repo(session: AsyncSession, repo_id: int) -> Repo | None:
    return await session.get(Repo, repo_id)


async def list_repos(session: AsyncSession, *, watched_only: bool = True) -> list[Repo]:
    stmt = select(Repo)
    if watched_only:
        stmt = stmt.where(Repo.watched.is_(True))
    return list((await session.scalars(stmt)).all())


async def list_workflows(session: AsyncSession, *, repo_id: int) -> list[Workflow]:
    stmt = select(Workflow).where(Workflow.repo_id == repo_id)
    return list((await session.scalars(stmt)).all())


async def list_runs(
    session: AsyncSession,
    *,
    repo_id: int | None = None,
    workflow_id: int | None = None,
    branch: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[WorkflowRun]:
    stmt = select(WorkflowRun)
    if repo_id is not None:
        stmt = stmt.where(WorkflowRun.repo_id == repo_id)
    if workflow_id is not None:
        stmt = stmt.where(WorkflowRun.workflow_id == workflow_id)
    if branch is not None:
        stmt = stmt.where(WorkflowRun.head_branch == branch)
    if status is not None:
        stmt = stmt.where(WorkflowRun.status == status)
    stmt = stmt.order_by(WorkflowRun.created_at.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())


async def open_findings(
    session: AsyncSession,
    *,
    repo_id: int | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
    limit: int = 1000,
) -> list[AuditFinding]:
    """Open (unresolved) findings, filtered in SQL and bounded by ``limit``."""
    stmt = select(AuditFinding).where(AuditFinding.resolved_at.is_(None))
    if repo_id is not None:
        stmt = stmt.where(AuditFinding.repo_id == repo_id)
    if severity is not None:
        stmt = stmt.where(AuditFinding.severity == severity)
    if finding_type is not None:
        stmt = stmt.where(AuditFinding.finding_type == finding_type)
    stmt = stmt.order_by(AuditFinding.last_seen_at.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())


async def upsert_finding(session: AsyncSession, row: dict[str, Any]) -> None:
    """Insert a finding or, if its fingerprint already exists, bump last_seen and reopen it."""
    now = datetime.now(UTC)
    values = {**row, "first_seen_at": now, "last_seen_at": now}
    stmt = _conflict_insert(session)(AuditFinding).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["fingerprint"],
        set_={
            "last_seen_at": now,
            "severity": values["severity"],
            "message": values["message"],
            "resolved_at": None,  # a finding seen again is no longer resolved
        },
    )
    await session.execute(stmt)


async def resolve_stale_findings(
    session: AsyncSession, *, repo_id: int, seen_fingerprints: set[str]
) -> None:
    """Mark open findings for a repo that were NOT seen this run as resolved."""
    stmt = (
        update(AuditFinding)
        .where(
            AuditFinding.repo_id == repo_id,
            AuditFinding.resolved_at.is_(None),
            AuditFinding.fingerprint.notin_(seen_fingerprints or [""]),
        )
        .values(resolved_at=datetime.now(UTC))
    )
    await session.execute(stmt)


async def upsert_workflow_relation(
    session: AsyncSession, *, repo_id: int, path: str, name: str | None, descriptor: dict[str, Any]
) -> None:
    """Insert/replace a workflow's relation descriptor, keyed by (repo_id, path)."""
    stmt = _conflict_insert(session)(WorkflowRelation).values(
        repo_id=repo_id, path=path, name=name, descriptor=descriptor
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["repo_id", "path"],
        set_={"name": name, "descriptor": descriptor},
    )
    await session.execute(stmt)


async def list_workflow_relations(session: AsyncSession) -> list[WorkflowRelation]:
    return list((await session.scalars(select(WorkflowRelation))).all())


async def list_templates(session: AsyncSession) -> list[WorkflowTemplate]:
    return list((await session.scalars(select(WorkflowTemplate))).all())


async def list_bindings(
    session: AsyncSession, *, repo_id: int | None = None
) -> list[TemplateBinding]:
    stmt = select(TemplateBinding)
    if repo_id is not None:
        stmt = stmt.where(TemplateBinding.repo_id == repo_id)
    return list((await session.scalars(stmt)).all())


async def upsert_template(
    session: AsyncSession, *, name: str, canonical_yaml: str
) -> WorkflowTemplate:
    """Create or replace a canonical template by name (bumps version on replace)."""
    existing = (
        await session.scalars(select(WorkflowTemplate).where(WorkflowTemplate.name == name))
    ).first()
    if existing is not None:
        existing.canonical_yaml = canonical_yaml
        existing.version += 1
        await session.flush()
        return existing
    tpl = WorkflowTemplate(name=name, canonical_yaml=canonical_yaml, version=1)
    session.add(tpl)
    await session.flush()
    return tpl


async def get_binding(session: AsyncSession, *, repo_id: int, path: str) -> TemplateBinding | None:
    stmt = select(TemplateBinding).where(
        TemplateBinding.repo_id == repo_id, TemplateBinding.path == path
    )
    return (await session.scalars(stmt)).first()


async def create_binding(
    session: AsyncSession, *, repo_id: int, template_id: int, path: str
) -> TemplateBinding:
    existing = await get_binding(session, repo_id=repo_id, path=path)
    if existing is not None:
        existing.template_id = template_id
        await session.flush()
        return existing
    binding = TemplateBinding(repo_id=repo_id, template_id=template_id, path=path)
    session.add(binding)
    await session.flush()
    return binding


async def update_binding_drift(
    session: AsyncSession, binding: TemplateBinding, *, severity: str
) -> None:
    binding.drift_severity = severity
    binding.last_drift_check_at = datetime.now(UTC)
    await session.flush()


async def create_campaign(
    session: AsyncSession,
    *,
    name: str,
    operation: str,
    params: dict | None,
    repo_ids: list[int],
    created_by: str | None = None,
) -> Campaign:
    campaign = Campaign(
        name=name,
        operation=operation,
        params=params,
        created_by=created_by,
        created_at=datetime.now(UTC),
        status="pending",
    )
    session.add(campaign)
    await session.flush()
    for rid in repo_ids:
        session.add(CampaignTarget(campaign_id=campaign.id, repo_id=rid, status="pending"))
    await session.flush()
    return campaign


async def get_campaign(session: AsyncSession, campaign_id: int) -> Campaign | None:
    return await session.get(Campaign, campaign_id)


async def list_targets(session: AsyncSession, *, campaign_id: int) -> list[CampaignTarget]:
    stmt = select(CampaignTarget).where(CampaignTarget.campaign_id == campaign_id)
    return list((await session.scalars(stmt)).all())


async def try_record_delivery(
    session: AsyncSession, *, delivery_id: str, event_type: str | None
) -> bool:
    """Atomically record a webhook delivery id. Returns True if newly recorded, False if duplicate.

    Backs the at-least-once → exactly-once dedup in the ingestor (review-2 #1). The unique
    primary key + ON CONFLICT DO NOTHING means even a TOCTOU race between two ingestor pods
    sees one True and one False.
    """
    stmt = (
        pg_insert(ProcessedDelivery)
        .values(delivery_id=delivery_id, event_type=event_type, seen_at=datetime.now(UTC))
        .on_conflict_do_nothing(index_elements=["delivery_id"])
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount == 1
