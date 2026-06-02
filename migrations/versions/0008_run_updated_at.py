"""workflow_runs: add updated_at to guard against out-of-order webhook redelivery

GitHub delivers workflow_run events at-least-once and out of order. The run's `updated_at`
is monotonic across state transitions, so storing it lets the upsert reject a stale event
(e.g. a late `in_progress`) that would otherwise regress a `completed` row. Nullable so the
column backfills lazily — pre-existing rows compare as "unknown age" and are updated once.

Revision ID: 0008_run_updated_at
Revises: 0007_findings_partial
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_run_updated_at"
down_revision: str | None = "0007_findings_partial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "updated_at")
