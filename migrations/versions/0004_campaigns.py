"""edit: campaigns + campaign_targets (PR-based bulk operations)

Revision ID: 0004_campaigns
Revises: 0003_templates
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_campaigns"
down_revision: str | None = "0003_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
    )
    op.create_table(
        "campaign_targets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.BigInteger(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("pr_number", sa.BigInteger(), nullable=True),
        sa.Column("pr_url", sa.String(512), nullable=True),
        sa.Column("diff_preview", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_campaign_targets_campaign_id", "campaign_targets", ["campaign_id"])


def downgrade() -> None:
    op.drop_table("campaign_targets")
    op.drop_table("campaigns")
