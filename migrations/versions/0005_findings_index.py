"""audit_findings: composite index for the open-findings query

Revision ID: 0005_find_idx
Revises: 0004_campaigns
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_find_idx"
down_revision: str | None = "0004_campaigns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # the hot path is "open findings for a repo" — index repo_id + resolved_at together
    op.create_index("ix_audit_findings_repo_resolved", "audit_findings", ["repo_id", "resolved_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_findings_repo_resolved", table_name="audit_findings")
