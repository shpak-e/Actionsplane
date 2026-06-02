"""drift: workflow_templates + template_bindings

Revision ID: 0003_templates
Revises: 0002_finding_fp
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_templates"
down_revision: str | None = "0002_finding_fp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("canonical_yaml", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.create_table(
        "template_bindings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column(
            "template_id", sa.BigInteger(), sa.ForeignKey("workflow_templates.id"), nullable=False
        ),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("last_drift_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drift_severity", sa.String(16), nullable=True),
    )
    op.create_index("ix_template_bindings_repo_id", "template_bindings", ["repo_id"])
    op.create_index("ix_template_bindings_template_id", "template_bindings", ["template_id"])


def downgrade() -> None:
    op.drop_table("template_bindings")
    op.drop_table("workflow_templates")
