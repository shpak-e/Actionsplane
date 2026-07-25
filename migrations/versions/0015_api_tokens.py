"""api_tokens: named tokens with roles (RBAC, roadmap #10)

Backs viewer/operator/admin role-based access. Only the SHA-256 of each token is stored (unique),
never the secret. These coexist with the legacy env tokens (ACTIONSPLANE_API_TOKEN → admin,
ACTIONSPLANE_API_READ_TOKEN → viewer), which keep working unchanged.

Revision ID: 0015_api_tokens
Revises: 0014_advisories
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_api_tokens"
down_revision: str | None = "0014_advisories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_api_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("api_tokens")
