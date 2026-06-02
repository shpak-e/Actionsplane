"""ingestor: delivery dedup table (review-2 fix)

Revision ID: 0006_deliveries
Revises: 0005_find_idx
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_deliveries"
down_revision: str | None = "0005_find_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_deliveries",
        sa.Column("delivery_id", sa.String(64), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("processed_deliveries")
