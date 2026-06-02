"""workflow_relations: per-workflow cross-workflow relation facts (Pipelines graph source)

Stores a compact descriptor (triggers, reusable calls, emits) per workflow so the Pipelines
view can assemble the fleet-wide trigger/dependency graph without re-fetching workflow files.

Revision ID: 0009_workflow_relations
Revises: 0008_run_updated_at
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_workflow_relations"
down_revision: str | None = "0008_run_updated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_relations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("descriptor", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("repo_id", "path", name="uq_workflow_relations_repo_path"),
    )


def downgrade() -> None:
    op.drop_table("workflow_relations")
