"""audit_findings: add path + fingerprint (dedup key) for finding lifecycle

Revision ID: 0002_finding_fp
Revises: 0001_initial
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_finding_fp"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_findings", sa.Column("path", sa.String(512), nullable=True))
    # nullable first so it applies to any existing rows, then unique-index it
    op.add_column("audit_findings", sa.Column("fingerprint", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_audit_findings_fingerprint", "audit_findings", ["fingerprint"])


def downgrade() -> None:
    op.drop_constraint("uq_audit_findings_fingerprint", "audit_findings", type_="unique")
    op.drop_column("audit_findings", "fingerprint")
    op.drop_column("audit_findings", "path")
