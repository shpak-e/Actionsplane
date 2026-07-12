"""Repository layer — async upsert + query helpers (plan §7, Phase 1).

Thin functions over the ORM so the ingestor/worker/API don't hand-roll SQL. Upserts use the
Postgres ``ON CONFLICT`` so replayed webhook deliveries (at-least-once) are idempotent — the
same run id simply updates the existing row. Queries return ORM instances; the API layer maps
them to response models.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from sqlalchemy import and_, case, delete, func, null, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.db.models import (
    AuditFinding,
    Campaign,
    CampaignTarget,
    Installation,
    Lease,
    ProcessedDelivery,
    Repo,
    TemplateBinding,
    Workflow,
    WorkflowJob,
    WorkflowRelation,
    WorkflowRun,
    WorkflowTemplate,
    WriteAuditLog,
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


async def upsert_run(session: AsyncSession, values: dict[str, Any]) -> int:
    """Upsert a run, never letting a stale event overwrite a fresher row. Returns rows written.

    GitHub delivers ``workflow_run`` events at-least-once and out of order, so a late
    ``in_progress`` redelivery can arrive *after* the ``completed`` event for the same run id.
    An unconditional upsert would regress the row from completed back to in-progress. The run's
    ``updated_at`` (monotonic across GitHub state transitions) gates the update.

    The guard is *strict* (review 3, 4a): it applies only when the incoming event is strictly
    newer, or exactly as new **and** actually changes ``status``/``conclusion`` (an equal-timestamp
    conclusion correction — the same nuance as the job gate). An identical redelivery — the common
    case for a reconcile sweep replaying already-seen runs — matches nothing and writes 0 rows, so
    an idle repo churns no rows and dirties no indexes. Legacy rows (``updated_at IS NULL``) still
    take the update. Staying in SQL keeps the check-and-write atomic under concurrency.
    """
    stmt = _conflict_insert(session)(WorkflowRun).values(**values)
    update_cols = {c: stmt.excluded[c] for c in values if c != "id"}
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_=update_cols,
        where=or_(
            WorkflowRun.updated_at.is_(None),
            WorkflowRun.updated_at < stmt.excluded["updated_at"],  # strictly newer → apply
            and_(  # same timestamp, but a real status/conclusion change (correction) → apply
                WorkflowRun.updated_at == stmt.excluded["updated_at"],
                or_(
                    WorkflowRun.status.is_distinct_from(stmt.excluded["status"]),
                    WorkflowRun.conclusion.is_distinct_from(stmt.excluded["conclusion"]),
                ),
            ),
        ),
    )
    result = await session.execute(stmt)
    return result.rowcount


async def upsert_installation(session: AsyncSession, values: dict[str, Any]) -> None:
    await _upsert(session, Installation, values)


# workflow_job lifecycle order. GitHub's job payloads carry no monotonic timestamp (unlike the
# run's updated_at), so ordering is gated on the status itself: queued < in_progress < completed.
_JOB_STATUS_RANK: dict[str | None, int] = {
    "queued": 0,
    "waiting": 0,
    "pending": 0,
    "in_progress": 1,
    "completed": 2,
}


async def upsert_job(session: AsyncSession, values: dict[str, Any]) -> None:
    """Upsert a job, but never let a stale event regress a fresher row (Phase 5.4).

    ``workflow_job`` events are delivered at-least-once and out of order, and — unlike runs —
    the payload has no monotonic ``updated_at`` to gate on. Instead the job *status* is ranked
    (queued=0 < in_progress=1 < completed=2) and the conditional upsert only applies when the
    incoming rank is >= the stored rank: a late ``in_progress`` redelivery can't reopen a
    ``completed`` job, while an equal-rank ``completed`` redelivery still lands (that's how a
    conclusion update on an already-completed row gets through). The stored rank is computed
    inline with a CASE so no extra column is needed, and the whole check-and-write stays one
    atomic SQL statement — mirroring the run guard from migration 0008. Dialect-portable
    (``ON CONFLICT ... DO UPDATE ... WHERE`` on both PG and sqlite).
    """
    stmt = _conflict_insert(session)(WorkflowJob).values(**values)
    update_cols = {c: stmt.excluded[c] for c in values if c != "id"}
    incoming_rank = _JOB_STATUS_RANK.get(values.get("status"), 0)
    stored_rank = case(
        (WorkflowJob.status == "completed", 2),
        (WorkflowJob.status == "in_progress", 1),
        else_=0,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"], set_=update_cols, where=stored_rank <= incoming_rank
    )
    await session.execute(stmt)


async def list_jobs(session: AsyncSession, *, run_id: int) -> list[WorkflowJob]:
    stmt = select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    return list((await session.scalars(stmt)).all())


async def list_all_workflows(session: AsyncSession) -> list[Workflow]:
    """Every workflow row across all repos (used to map (repo_id, path) → workflow id)."""
    return list((await session.scalars(select(Workflow))).all())


class LatestRun(NamedTuple):
    """The few run columns the Pipelines graph needs — deliberately *not* the whole ORM row, so
    the heavy ``raw_payload`` JSONB is never fetched for a status annotation (Phase 5.3 / review 3).
    """

    id: int
    workflow_id: int
    status: str | None
    conclusion: str | None
    run_number: int


async def latest_runs_for(session: AsyncSession, workflow_ids: list[int]) -> dict[int, LatestRun]:
    """The most recent run per workflow id (by ``created_at``), for the given workflow ids.

    One indexed query regardless of history size: a ``ROW_NUMBER() OVER (PARTITION BY workflow_id
    ORDER BY created_at DESC)`` window keeps just the newest row per workflow, instead of streaming
    every run for those workflows into Python. Only the status columns are selected — never
    ``raw_payload``. The window form is dialect-portable (PG + sqlite ≥ 3.25). Returns
    ``{workflow_id: LatestRun}``.
    """
    if not workflow_ids:
        return {}
    rn = func.row_number().over(
        partition_by=WorkflowRun.workflow_id,
        order_by=(WorkflowRun.created_at.desc(), WorkflowRun.id.desc()),  # id breaks created ties
    )
    ranked = (
        select(
            WorkflowRun.id,
            WorkflowRun.workflow_id,
            WorkflowRun.status,
            WorkflowRun.conclusion,
            WorkflowRun.run_number,
            rn.label("rn"),
        )
        .where(WorkflowRun.workflow_id.in_(workflow_ids))
        .subquery()
    )
    stmt = select(
        ranked.c.id, ranked.c.workflow_id, ranked.c.status, ranked.c.conclusion, ranked.c.run_number
    ).where(ranked.c.rn == 1)
    rows = (await session.execute(stmt)).all()
    return {
        row.workflow_id: LatestRun(
            row.id, row.workflow_id, row.status, row.conclusion, row.run_number
        )
        for row in rows
    }


async def list_failing_jobs(session: AsyncSession, run_ids: list[int]) -> list[WorkflowJob]:
    """All failed jobs across the given runs, in one query, ordered ``(run_id, id)`` (review 3).

    Replaces the per-run job fetch behind the Pipelines "which step failed?" annotation: the caller
    groups by ``run_id`` and takes the first failing job per run. ``raw_payload`` is loaded (the
    step list lives there) but only for *failed* jobs of *failed* runs, so it stays bounded.
    """
    if not run_ids:
        return []
    stmt = (
        select(WorkflowJob)
        .where(WorkflowJob.run_id.in_(run_ids), WorkflowJob.conclusion == "failure")
        .order_by(WorkflowJob.run_id, WorkflowJob.id)
    )
    return list((await session.scalars(stmt)).all())


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


def _open_findings_filter(stmt, *, repo_id, severity, finding_type):
    """Apply the open-findings predicate shared by the list / count / paginate paths."""
    stmt = stmt.where(AuditFinding.resolved_at.is_(None))
    if repo_id is not None:
        stmt = stmt.where(AuditFinding.repo_id == repo_id)
    if severity is not None:
        stmt = stmt.where(AuditFinding.severity == severity)
    if finding_type is not None:
        stmt = stmt.where(AuditFinding.finding_type == finding_type)
    return stmt


async def open_findings(
    session: AsyncSession,
    *,
    repo_id: int | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[AuditFinding]:
    """A page of open (unresolved) findings, filtered in SQL, newest first."""
    stmt = _open_findings_filter(
        select(AuditFinding), repo_id=repo_id, severity=severity, finding_type=finding_type
    )
    stmt = stmt.order_by(AuditFinding.last_seen_at.desc()).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def count_open_findings(
    session: AsyncSession,
    *,
    repo_id: int | None = None,
    severity: str | None = None,
    finding_type: str | None = None,
) -> int:
    """Total open findings matching the filters — the ``total`` for a paginated ``/findings``."""
    stmt = _open_findings_filter(
        select(func.count()).select_from(AuditFinding),
        repo_id=repo_id,
        severity=severity,
        finding_type=finding_type,
    )
    return int((await session.scalar(stmt)) or 0)


async def count_open_findings_grouped(session: AsyncSession) -> list[tuple[str, str, int]]:
    """``(severity, finding_type, count)`` for all open findings — grouped in SQL (review 3, P1.4).

    The scorecard rolls these counts up instead of fetching (and capping) rows, so it stays exact
    no matter how many findings are open. Rides the 0007 partial index on ``resolved_at IS NULL``.
    """
    stmt = (
        select(AuditFinding.severity, AuditFinding.finding_type, func.count().label("n"))
        .where(AuditFinding.resolved_at.is_(None))
        .group_by(AuditFinding.severity, AuditFinding.finding_type)
    )
    return [(r.severity, r.finding_type, r.n) for r in (await session.execute(stmt)).all()]


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


async def record_write_audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one row to the write-audit trail (Phase 5.2). Insert-only — no update path exists.

    The caller owns the commit so the audit row lands in the same transaction as the write it
    describes where possible. ``detail`` must be JSON-serializable.
    """
    session.add(
        WriteAuditLog(
            occurred_at=datetime.now(UTC), actor=actor, action=action, target=target, detail=detail
        )
    )
    await session.flush()


async def list_write_audit(
    session: AsyncSession, *, limit: int = 100, offset: int = 0
) -> list[WriteAuditLog]:
    """The audit trail, newest first, paginated for the API."""
    stmt = (
        select(WriteAuditLog)
        .order_by(WriteAuditLog.occurred_at.desc(), WriteAuditLog.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def claim_lease(session: AsyncSession, *, name: str, holder: str, ttl_seconds: int) -> bool:
    """Atomically claim/refresh a named lease. True iff ``holder`` now owns it (Phase 5.3).

    One conditional upsert: insert the lease, or take it over iff it has expired or is already
    held by this claimant (re-claiming refreshes the TTL — a cheap heartbeat). The condition
    lives in the ``ON CONFLICT ... WHERE`` so two workers racing on the same tick see exactly
    one True — the same atomicity argument as ``try_record_delivery``. Dialect-portable.
    """
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    stmt = _conflict_insert(session)(Lease).values(name=name, holder=holder, expires_at=expires_at)
    stmt = stmt.on_conflict_do_update(
        index_elements=["name"],
        set_={"holder": holder, "expires_at": expires_at},
        where=or_(Lease.expires_at <= now, Lease.holder == holder),
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount == 1


async def prune_run_payloads(
    session: AsyncSession, *, cutoff: datetime, batch_size: int = 500
) -> int:
    """Null ``raw_payload`` on workflow_runs created before ``cutoff`` (Phase 5.6).

    The normalized columns stay, so run history remains queryable — only the bulky JSONB goes.
    Batched (select ids LIMIT n → targeted UPDATE → commit, repeat) so a first run over a large
    backlog never holds a long lock. Returns the number of rows pruned.
    """
    pruned = 0
    while True:
        ids = (
            await session.scalars(
                select(WorkflowRun.id)
                .where(WorkflowRun.raw_payload.is_not(None), WorkflowRun.created_at < cutoff)
                .limit(batch_size)
            )
        ).all()
        if not ids:
            return pruned
        # null() forces SQL NULL — a bare None would bind as the JSON 'null' *value*
        # (none_as_null=False), leaving the row IS NOT NULL and this loop spinning forever.
        await session.execute(
            update(WorkflowRun).where(WorkflowRun.id.in_(ids)).values(raw_payload=null())
        )
        await session.commit()
        pruned += len(ids)


async def prune_job_payloads(
    session: AsyncSession, *, cutoff: datetime, batch_size: int = 500
) -> int:
    """Null ``raw_payload`` on workflow_jobs that finished (or started) before ``cutoff``.

    Jobs have no created_at; age is judged by ``completed_at``, falling back to ``started_at``.
    Rows with neither timestamp are left alone — they can't be aged. Batched like the run prune.
    """
    age = case(
        (WorkflowJob.completed_at.is_not(None), WorkflowJob.completed_at),
        else_=WorkflowJob.started_at,
    )
    pruned = 0
    while True:
        ids = (
            await session.scalars(
                select(WorkflowJob.id)
                .where(WorkflowJob.raw_payload.is_not(None), age < cutoff)
                .limit(batch_size)
            )
        ).all()
        if not ids:
            return pruned
        await session.execute(
            update(WorkflowJob).where(WorkflowJob.id.in_(ids)).values(raw_payload=null())
        )
        await session.commit()
        pruned += len(ids)


async def prune_deliveries(
    session: AsyncSession, *, cutoff: datetime, batch_size: int = 500
) -> int:
    """Delete processed webhook delivery ids seen before ``cutoff`` (dedup horizon, Phase 5.6).

    GitHub redeliveries arrive within days, not months — old ids only bloat the table. Batched
    deletes for the same no-long-locks reason as the payload prunes.
    """
    pruned = 0
    while True:
        ids = (
            await session.scalars(
                select(ProcessedDelivery.delivery_id)
                .where(ProcessedDelivery.seen_at < cutoff)
                .limit(batch_size)
            )
        ).all()
        if not ids:
            return pruned
        await session.execute(
            delete(ProcessedDelivery).where(ProcessedDelivery.delivery_id.in_(ids))
        )
        await session.commit()
        pruned += len(ids)
