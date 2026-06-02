"""audit_findings: partial index for the org-wide severity scorecard query (review-2)

Revision ID: 0007_findings_partial
Revises: 0006_deliveries
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_findings_partial"
down_revision: str | None = "0006_deliveries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `open_findings(severity=...)` org-wide (no repo_id) — the (repo_id, resolved_at) index from
    # 0005 doesn't help. A partial index covering only open rows is small and exactly fits the
    # scorecard query: `WHERE resolved_at IS NULL AND severity = ?  ORDER BY last_seen_at DESC`.
    op.execute(
        "CREATE INDEX ix_audit_findings_open_by_sev "
        "ON audit_findings (severity, last_seen_at DESC) "
        "WHERE resolved_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_findings_open_by_sev")
