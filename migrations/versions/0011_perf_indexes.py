"""perf indexes for the run / prune / dedup hot paths (review 3, P1.2 + P1.3)

Five indexes the read and retention paths need at scale (~90M runs / ~270M jobs projected):

* ``(repo_id, created_at DESC)`` and ``(workflow_id, created_at DESC)`` — the ``/runs`` and
  ``/pipelines`` top-N-by-recency queries (the plain single-column indexes from 0001 don't
  order-cover the DESC scan).
* two partial indexes over ``raw_payload IS NOT NULL`` — the retention prune (Phase 5.6) scans
  for rows that still carry a payload; a partial index is small and exactly matches that predicate.
  The jobs one keys on ``COALESCE(completed_at, started_at)`` to match the prune's age expression.
* ``processed_deliveries(seen_at)`` — the delivery-retention prune's age scan.

On Postgres each index is built ``CONCURRENTLY`` (no ACCESS EXCLUSIVE lock against writes on these
hot tables), which must run outside a transaction — hence the per-index ``autocommit_block``. On
sqlite (hermetic tests) plain in-transaction creates; partial + expression indexes work there too.

Revision ID: 0011_perf_indexes
Revises: 0010_write_audit_leases
Create Date: 2026-07-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_perf_indexes"
down_revision: str | None = "0010_write_audit_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index name, table, definition suffix). One source of truth for upgrade + downgrade.
_INDEXES: list[tuple[str, str, str]] = [
    ("ix_workflow_runs_repo_created", "workflow_runs", "(repo_id, created_at DESC)"),
    ("ix_workflow_runs_workflow_created", "workflow_runs", "(workflow_id, created_at DESC)"),
    ("ix_workflow_runs_payload_prune", "workflow_runs", "(created_at) WHERE raw_payload IS NOT NULL"),
    (
        "ix_workflow_jobs_completed_prune",
        "workflow_jobs",
        "(COALESCE(completed_at, started_at)) WHERE raw_payload IS NOT NULL",
    ),
    ("ix_processed_deliveries_seen_at", "processed_deliveries", "(seen_at)"),
]


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        for name, table, defn in _INDEXES:
            with op.get_context().autocommit_block():
                op.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {table} {defn}")
    else:
        for name, table, defn in _INDEXES:
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {defn}")


def downgrade() -> None:
    if _is_postgres():
        for name, _table, _defn in reversed(_INDEXES):
            with op.get_context().autocommit_block():
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    else:
        for name, _table, _defn in reversed(_INDEXES):
            op.execute(f"DROP INDEX IF EXISTS {name}")
