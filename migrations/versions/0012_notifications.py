"""notification_channels + notification_sent: alert routing (Slack / webhook / email)

* ``notification_channels`` — an operator's alert destinations and which event types each
  subscribes to. Secrets (webhook URL, recipient) live in the JSONB ``config``; the API masks them.
* ``notification_sent`` — idempotency ledger keyed by a stable per-event key, so an event fires an
  alert exactly once (a finding re-seen every audit, a deprecation still urgent every sweep).

Revision ID: 0012_notifications
Revises: 0011_perf_indexes
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_notifications"
down_revision: str | None = "0011_perf_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "notification_sent",
        sa.Column("event_key", sa.String(255), primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("notification_sent")
    op.drop_table("notification_channels")
