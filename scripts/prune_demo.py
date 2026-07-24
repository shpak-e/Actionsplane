"""Delete the demo dataset that ``seed_local.py`` writes — the inverse of that script.

Once a real GitHub App is ingesting live repos, the seed's demo installation (1001) and its
``demo-org`` repos are just noise: they can't mint an installation token, so every reconcile
sweep logs a harmless 404 for them. This removes them and everything hanging off them, in
foreign-key order, scoped strictly to the demo installation/org so live data is never touched.

Idempotent. Usage (from the project root, against ACTIONSPLANE_DATABASE_URL):

    PYTHONPATH=src python scripts/prune_demo.py
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from actionsplane.db.base import get_engine, get_sessionmaker
from actionsplane.db.models import (
    AuditFinding,
    CampaignTarget,
    Installation,
    Repo,
    TemplateBinding,
    Workflow,
    WorkflowJob,
    WorkflowRelation,
    WorkflowRun,
)

# Must match seed_local.py.
INSTALLATION_ID = 1001
DEMO_ORG = "demo-org"


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


async def prune() -> None:
    engine = get_engine()
    maker = get_sessionmaker()
    async with maker() as s:
        # Demo repos = anything under the demo org or the seed installation (belt + suspenders).
        repo_ids = list(
            (
                await s.scalars(
                    select(Repo.id).where(
                        (Repo.owner == DEMO_ORG) | (Repo.installation_id == INSTALLATION_ID)
                    )
                )
            ).all()
        )
        run_ids = (
            list(
                (
                    await s.scalars(select(WorkflowRun.id).where(WorkflowRun.repo_id.in_(repo_ids)))
                ).all()
            )
            if repo_ids
            else []
        )

        counts: dict[str, int] = {}
        if run_ids:
            counts["workflow_jobs"] = (
                await s.execute(delete(WorkflowJob).where(WorkflowJob.run_id.in_(run_ids)))
            ).rowcount
        if repo_ids:
            # Children of repos, before the repos themselves.
            for model in (
                WorkflowRun,
                AuditFinding,
                TemplateBinding,
                WorkflowRelation,
                Workflow,
                CampaignTarget,
            ):
                counts[model.__tablename__] = (
                    await s.execute(delete(model).where(model.repo_id.in_(repo_ids)))
                ).rowcount
            counts["repos"] = (await s.execute(delete(Repo).where(Repo.id.in_(repo_ids)))).rowcount
        counts["github_installations"] = (
            await s.execute(delete(Installation).where(Installation.id == INSTALLATION_ID))
        ).rowcount
        await s.commit()

    await engine.dispose()
    total = sum(counts.values())
    if not total:
        print("no demo data found — nothing to prune")
        return
    print(f"pruned {len(repo_ids)} demo repos + {total} rows:")
    for table, n in counts.items():
        if n:
            print(f"  {table}: {n}")


if __name__ == "__main__":
    asyncio.run(prune())
