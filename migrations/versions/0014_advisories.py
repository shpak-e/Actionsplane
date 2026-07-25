"""advisories: GHSA advisory-watch inventory (roadmap #7)

Persists GitHub security advisories for the ``actions`` ecosystem, polled from the global advisory
DB, one row per ``(ghsa_id, package)``. The advisory-watch sweep matches ``package`` against the
action refs the fleet uses and alerts on hits; a table (rather than a per-request fetch) keeps the
watch endpoint cheap and lets a poll failure fall back to the last-known set.

Revision ID: 0014_advisories
Revises: 0013_campaign_findings
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_advisories"
down_revision: str | None = "0013_campaign_findings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "advisories",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ghsa_id", sa.String(64), nullable=False),
        sa.Column("package", sa.String(255), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("vulnerable_range", sa.String(255), nullable=True),
        sa.Column("first_patched", sa.String(64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ghsa_id", "package", name="uq_advisory_ghsa_package"),
    )
    # The watch join filters/looks up by affected package (action owner/repo).
    op.create_index("ix_advisories_package", "advisories", ["package"])


def downgrade() -> None:
    op.drop_index("ix_advisories_package", table_name="advisories")
    op.drop_table("advisories")
