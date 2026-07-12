"""write_audit_log + leases: trust hardening (plan §14, Phase 5.2 + 5.3)

Two independent tables shipped together as the Phase 5 trust-hardening schema:

* ``write_audit_log`` — append-only who/what/when trail for every write operation (campaign
  create/apply, run re-run, SARIF upload, offline sync). Indexed on ``occurred_at`` for the
  newest-first paginated API read.
* ``leases`` — named TTL leases claimed by an atomic conditional upsert, so the cron sweeps
  (reconcile / audit / drift / retention pruning) stay single-flight when the worker runs with
  more than one replica.

Revision ID: 0010_write_audit_leases
Revises: 0009_workflow_relations
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_write_audit_leases"
down_revision: str | None = "0009_workflow_relations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "write_audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(512), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_write_audit_log_occurred_at", "write_audit_log", ["occurred_at"])
    op.create_table(
        "leases",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("holder", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("leases")
    op.drop_index("ix_write_audit_log_occurred_at", table_name="write_audit_log")
    op.drop_table("write_audit_log")
