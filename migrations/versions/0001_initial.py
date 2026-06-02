"""initial schema — installations, repos, workflows, runs, jobs, audit findings

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_installations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("account_login", sa.String(255), nullable=False),
        sa.Column("account_type", sa.String(32), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "repos",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "installation_id",
            sa.BigInteger(),
            sa.ForeignKey("github_installations.id"),
            nullable=False,
        ),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("default_branch", sa.String(255), nullable=False, server_default="main"),
        sa.Column("watched", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_repos_installation_id", "repos", ["installation_id"])
    op.create_table(
        "workflows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="active"),
        sa.Column("last_modified_sha", sa.String(64), nullable=True),
        sa.Column("parsed_ast", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_workflows_repo_id", "workflows", ["repo_id"])
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("workflow_id", sa.BigInteger(), sa.ForeignKey("workflows.id"), nullable=True),
        sa.Column("run_number", sa.BigInteger(), nullable=False),
        sa.Column("head_branch", sa.String(255), nullable=True),
        sa.Column("head_sha", sa.String(64), nullable=True),
        sa.Column("event", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("conclusion", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor", sa.String(255), nullable=True),
        sa.Column("run_attempt", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_workflow_runs_repo_id", "workflow_runs", ["repo_id"])
    op.create_index("ix_workflow_runs_workflow_id", "workflow_runs", ["workflow_id"])
    op.create_index("ix_workflow_runs_created_at", "workflow_runs", ["created_at"])
    op.create_table(
        "workflow_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("conclusion", sa.String(32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runner_name", sa.String(255), nullable=True),
        sa.Column("runner_group", sa.String(255), nullable=True),
        sa.Column("labels", postgresql.JSONB(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_workflow_jobs_run_id", "workflow_jobs", ["run_id"])
    op.create_table(
        "audit_findings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("workflow_id", sa.BigInteger(), sa.ForeignKey("workflows.id"), nullable=True),
        sa.Column("finding_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("ref", sa.String(512), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_audit_findings_repo_id", "audit_findings", ["repo_id"])


def downgrade() -> None:
    op.drop_table("audit_findings")
    op.drop_table("workflow_jobs")
    op.drop_table("workflow_runs")
    op.drop_table("workflows")
    op.drop_table("repos")
    op.drop_table("github_installations")
