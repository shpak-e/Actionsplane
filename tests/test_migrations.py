"""Migration-graph invariants (pure — no DB).

The real "migrations apply on Postgres" gate lives in CI (the ``migrations`` job runs
``alembic upgrade head`` against a postgres:16 service, then round-trips the newest one).
Those need a real engine because the schema uses PG-native types (JSONB, BigInteger).

Here we cheaply assert the *graph* stays sane so a merge accident is caught in-process:
exactly one head (two migration files sharing a down_revision from parallel PRs would make
two, and ``alembic upgrade head`` then errors on "multiple heads") and a single unbroken
down-revision chain from that head to base.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _script_dir() -> ScriptDirectory:
    cfg = Config()
    migrations = Path(__file__).resolve().parent.parent / "migrations"
    cfg.set_main_option("script_location", str(migrations))
    return ScriptDirectory.from_config(cfg)


def test_single_head():
    heads = _script_dir().get_heads()
    assert len(heads) == 1, f"divergent migration heads {heads} — an `alembic merge` is needed"


def test_chain_is_linear_to_base():
    revs = list(_script_dir().walk_revisions())  # head -> ... -> base
    for newer, older in pairwise(revs):
        downs = newer.down_revision
        downs = downs if isinstance(downs, tuple) else (downs,)
        assert older.revision in downs, f"{newer.revision} does not chain onto {older.revision}"
    assert revs[-1].down_revision is None, "chain does not terminate at base"
