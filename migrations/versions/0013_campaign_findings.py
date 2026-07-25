"""campaign_findings: the verified-closed loop (D2/P1)

Snapshots the finding fingerprints a campaign's operation targets, at campaign-create time. After
the campaign's PRs merge and the repo is re-audited, a targeted finding whose ``audit_findings`` row
is now ``resolved_at``-stamped counts as verified-closed — that join is how a campaign reports
"resolved 41/47" (proof the fix landed, not merely that a PR opened).

Revision ID: 0013_campaign_findings
Revises: 0012_notifications
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_campaign_findings"
down_revision: str | None = "0012_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaign_findings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.BigInteger(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("repos.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("finding_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.UniqueConstraint("campaign_id", "fingerprint", name="uq_campaign_fingerprint"),
    )
    # Verification joins by fingerprint; the count query filters by campaign_id.
    op.create_index("ix_campaign_findings_campaign", "campaign_findings", ["campaign_id"])
    op.create_index("ix_campaign_findings_fingerprint", "campaign_findings", ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_campaign_findings_fingerprint", table_name="campaign_findings")
    op.drop_index("ix_campaign_findings_campaign", table_name="campaign_findings")
    op.drop_table("campaign_findings")
