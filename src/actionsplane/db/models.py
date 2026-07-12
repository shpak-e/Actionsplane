"""ORM models mirroring the data-model sketch in plan §7.

Event-sourced run history: every workflow_run / workflow_job event is stored with its raw
JSONB payload; fast metrics come from materialised views (defined in migrations, not here).
This module defines the core relational tables only — enough to scaffold Phase 1 migrations.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from actionsplane.db.base import Base


class Installation(Base):
    __tablename__ = "github_installations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # GitHub installation id
    account_login: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[str] = mapped_column(String(32))  # User | Organization
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    repos: Mapped[list[Repo]] = relationship(back_populates="installation")


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # GitHub repo id
    installation_id: Mapped[int] = mapped_column(ForeignKey("github_installations.id"))
    owner: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    watched: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    installation: Mapped[Installation] = relationship(back_populates="repos")


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    path: Mapped[str] = mapped_column(String(512))
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="active")
    last_modified_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parsed_ast: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # GitHub run id
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id"), nullable=True)
    run_number: Mapped[int] = mapped_column(BigInteger)
    head_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # GitHub's run `updated_at` — monotonic across state transitions. Gates the upsert so a
    # late, out-of-order webhook redelivery can't regress a fresher row (see upsert_run).
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run_attempt: Mapped[int] = mapped_column(BigInteger, default=1)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class AuditFinding(Base):
    __tablename__ = "audit_findings"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id"), nullable=True)
    finding_type: Mapped[str] = mapped_column(String(64))  # see models.enums.FindingType
    severity: Mapped[str] = mapped_column(String(16))
    path: Mapped[str | None] = mapped_column(String(512), nullable=True)  # workflow file path
    ref: Mapped[str | None] = mapped_column(String(512), nullable=True)  # the uses: string, etc.
    message: Mapped[str] = mapped_column(Text)
    # stable dedup key = sha256(repo_id:path:finding_type:ref); one row per distinct finding
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowJob(Base):
    __tablename__ = "workflow_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # GitHub job id
    run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"))
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    runner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    runner_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    labels: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    name: Mapped[str] = mapped_column(String(255))  # e.g. "ci.yml"
    canonical_yaml: Mapped[str] = mapped_column(Text)  # raw YAML; parsed to AST on demand
    version: Mapped[int] = mapped_column(BigInteger, default=1)


class TemplateBinding(Base):
    __tablename__ = "template_bindings"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    template_id: Mapped[int] = mapped_column(ForeignKey("workflow_templates.id"))
    path: Mapped[str] = mapped_column(String(512))  # which workflow file maps to the template
    last_drift_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    drift_severity: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    name: Mapped[str] = mapped_column(String(255))
    operation: Mapped[str] = mapped_column(String(64))  # e.g. pin-shas, set-permissions
    params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="pending")


class CampaignTarget(Base):
    __tablename__ = "campaign_targets"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"))
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    pr_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    diff_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorkflowRelation(Base):
    """Per-workflow cross-workflow relation facts (triggers/calls/emits), persisted during a
    sweep so the Pipelines graph builds without re-fetching files. One row per (repo, path)."""

    __tablename__ = "workflow_relations"
    __table_args__ = (UniqueConstraint("repo_id", "path", name="uq_workflow_relations_repo_path"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"))
    path: Mapped[str] = mapped_column(String(512))
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    descriptor: Mapped[dict] = mapped_column(JSONB)


class ProcessedDelivery(Base):
    __tablename__ = "processed_deliveries"

    # X-GitHub-Delivery is a UUID — at-least-once delivery dedup key (review-2 #1 fix).
    delivery_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WriteAuditLog(Base):
    """Append-only who/what/when trail for every write operation (plan §8, Phase 5.2).

    One row per mutation (campaign create/apply, run re-run, SARIF upload, offline sync, …).
    ``actor`` is the token label that performed it ("operate" | "read" | "worker"); ``detail``
    carries the operation-specific evidence (PR URLs, repo ids, analysis URL). Never updated or
    deleted by application code — the repository layer only exposes insert + list.
    """

    __tablename__ = "write_audit_log"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))  # e.g. campaign.apply, run.rerun
    target: Mapped[str | None] = mapped_column(String(512), nullable=True)  # repo/campaign/run id
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Lease(Base):
    """Named, TTL'd lease so cron sweeps stay single-flight at worker replicas > 1 (Phase 5.3).

    Claimed via an atomic conditional upsert (see ``repository.claim_lease``): the row is taken
    iff it is free, expired, or already held by the claimant. Portable across PG/sqlite — no
    advisory locks, no Redis dependency in the correctness path.
    """

    __tablename__ = "leases"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. "sweep:reconcile"
    holder: Mapped[str] = mapped_column(String(255))  # host:pid of the claiming worker
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
