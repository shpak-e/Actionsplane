"""Seed the local database with demo installation, repos, workflows, runs, findings, and drift.

ActionsPlane onboards repos through the GitHub App install webhook — there is no manual
"add repo" endpoint by design (onboarding is install-driven; see docs/USER_GUIDE.md). That
makes it awkward to *look at* the dashboard/API locally before you've wired up a real App and
a public webhook tunnel. This script fills that gap with a realistic, varied dataset written
straight into the DB via the same repository upserts the worker uses, so the read API and UI
light up immediately — including the per-workflow metrics panel (which needs `workflows` rows
and runs that carry a `workflow_id`).

It does NOT exercise the real ingest → audit → PR path (that needs GitHub credentials). It is
purely a local "make the read model non-empty" convenience. Idempotent — safe to re-run.

Usage (from the project root, against whatever ACTIONSPLANE_DATABASE_URL points at):

    # against the docker-compose Postgres:
    PYTHONPATH=src python scripts/seed_local.py

    # against a throwaway sqlite file (no Postgres needed):
    ACTIONSPLANE_DATABASE_URL=sqlite+aiosqlite:///./local.db PYTHONPATH=src \
        python scripts/seed_local.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from actionsplane.audit.findings import Finding
from actionsplane.db.base import Base, get_engine, get_sessionmaker
from actionsplane.db.models import Workflow
from actionsplane.db.repository import (
    create_binding,
    update_binding_drift,
    upsert_finding,
    upsert_installation,
    upsert_job,
    upsert_repo,
    upsert_run,
    upsert_template,
    upsert_workflow_relation,
)
from actionsplane.ingestor import events
from actionsplane.models.enums import FindingType, Severity


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    """Render the Postgres-only JSONB columns as JSON so the sqlite convenience path works."""
    return "JSON"


INSTALLATION_ID = 1001
DEMO_ORG = "demo-org"
WORKFLOW_PATH = ".github/workflows/ci.yml"

# Canonical CI template (so the Drift tab has a template + bindings to score against).
CANONICAL_CI = (
    "name: CI\n"
    "on: [push]\n"
    "jobs:\n"
    "  build:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n"
)

F = FindingType
S = Severity

# Each repo: a run "pattern" (status, conclusion, age_min, duration_s) per run, the findings to
# raise, and the drift severity of its ci.yml binding. mobile-app is intentionally clean + green.
REPOS = [
    {
        "id": 5001,
        "name": "payments-api",
        "branch": "main",
        "runs": [
            ("completed", "success", 8, 192),
            ("completed", "success", 64, 205),
            ("completed", "failure", 130, 88),
            ("completed", "success", 190, 210),
            ("in_progress", None, 2, None),
        ],
        "findings": [
            (
                F.DANGEROUS_SECRET_FLOW,
                S.CRITICAL,
                "aws-actions/configure-aws-credentials@v2",
                "A secret is forwarded to a third-party action; review the trust boundary.",
            ),
            (
                F.UNPINNED_ACTION,
                S.HIGH,
                "actions/checkout@v3",
                "Action pinned to a mutable tag; pin to a commit SHA.",
            ),
            (
                F.MISSING_PERMISSIONS,
                S.MEDIUM,
                None,
                "Workflow has no top-level permissions: block (defaults to broad scopes).",
            ),
        ],
        "drift": "content",
    },
    {
        "id": 5002,
        "name": "web-frontend",
        "branch": "main",
        "runs": [
            ("completed", "success", 14, 121),
            ("completed", "success", 90, 119),
            ("completed", "failure", 150, 47),
            ("completed", "failure", 220, 51),
            ("queued", None, 1, None),
        ],
        "findings": [
            (
                F.BROAD_PERMISSIONS,
                S.HIGH,
                None,
                "`permissions: write-all` grants more than this workflow needs.",
            ),
            (
                F.DEPRECATED_ACTION,
                S.LOW,
                "actions/upload-artifact@v2",
                "actions/upload-artifact@v2 is deprecated; upgrade to v4.",
            ),
        ],
        "drift": "structural",
    },
    {
        "id": 5003,
        "name": "infra-terraform",
        "branch": "main",
        "runs": [
            ("completed", "success", 30, 410),
            ("completed", "success", 300, 398),
            ("completed", "success", 600, 405),
        ],
        "findings": [
            (
                F.UNPINNED_ACTION,
                S.HIGH,
                "hashicorp/setup-terraform@v3",
                "Action pinned to a mutable tag; pin to a commit SHA.",
            ),
            (
                F.UNVERIFIED_PUBLISHER,
                S.MEDIUM,
                "some-org/custom-deploy@v1",
                "Action publisher is not on the trusted allowlist.",
            ),
            (
                F.MISSING_CONCURRENCY,
                S.LOW,
                None,
                "No concurrency: group — concurrent deploys can race.",
            ),
        ],
        "drift": "minor",
    },
    {
        "id": 5004,
        "name": "mobile-app",
        "branch": "main",
        "runs": [
            ("completed", "success", 22, 363),
            ("completed", "success", 180, 351),
        ],
        "findings": [],  # clean repo — shows a green/empty posture for contrast
        "drift": "identical",
    },
]


# A demo cross-repo pipeline for the Pipelines tab:
#   payments-api CI --triggers--> payments-api Deploy --opens-pr--> infra-terraform
#   web-frontend Release --calls--> infra-terraform Apply (reusable workflow)
def _rel(name, *, triggers, upstreams=(), reusable=False, dispatch=False, calls=(), emits=()):
    return {
        "name": name,
        "triggers": list(triggers),
        "workflow_run_upstreams": list(upstreams),
        "is_reusable": reusable,
        "accepts_dispatch": dispatch,
        "dispatch_types": ["deploy"] if dispatch else [],
        "calls": list(calls),
        "emits": list(emits),
    }


RELATIONS = [
    (5001, ".github/workflows/ci.yml", _rel("CI", triggers=["push", "pull_request"])),
    (
        5001,
        ".github/workflows/deploy.yml",
        _rel(
            "Deploy",
            triggers=["workflow_run"],
            upstreams=["CI"],
            emits=[
                {
                    "kind": "opens-pr",
                    "target_repo": "demo-org/infra-terraform",
                    "detail": "peter-evans/create-pull-request",
                }
            ],
        ),
    ),
    (
        5003,
        ".github/workflows/apply.yml",
        _rel(
            "Apply", triggers=["workflow_call", "repository_dispatch"], reusable=True, dispatch=True
        ),
    ),
    (
        5002,
        ".github/workflows/release.yml",
        _rel(
            "Release",
            triggers=["push"],
            calls=[{"repo": "demo-org/infra-terraform", "path": ".github/workflows/apply.yml"}],
        ),
    ),
]


def _run_object(run_id: int, repo: dict, shape: tuple, workflow_id: int) -> dict:
    """A GitHub-shaped run object `normalize_run_object` can consume."""
    status, conclusion, age_min, duration_s = shape
    created = datetime.now(UTC) - timedelta(minutes=age_min)
    started = created + timedelta(seconds=9)
    updated = started + timedelta(seconds=duration_s if duration_s else 20)
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "run_number": run_id % 1000,
        "head_branch": repo["branch"],
        "head_sha": f"{run_id:040x}"[:40],
        "event": "push",
        "status": status,
        "conclusion": conclusion,
        "created_at": created.isoformat(),
        "run_started_at": started.isoformat(),
        "updated_at": updated.isoformat(),
        "actor": {"login": "octocat"},
        "run_attempt": 1,
    }


def _steps(pairs: list[tuple[str, str | None]]) -> list[dict]:
    """Build GitHub-shaped step objects from (name, conclusion) pairs. ``conclusion`` is one of
    success/failure/skipped, ``"in_progress"`` for a running step, or None for a queued one."""
    out = []
    for i, (name, concl) in enumerate(pairs, start=1):
        if concl == "in_progress":
            out.append({"name": name, "status": "in_progress", "conclusion": None, "number": i})
        elif concl is None:
            out.append({"name": name, "status": "queued", "conclusion": None, "number": i})
        else:
            out.append({"name": name, "status": "completed", "conclusion": concl, "number": i})
    return out


def _ci_steps(status: str, conclusion: str | None) -> list[dict]:
    """Steps for a generic CI job, shaped by the run outcome (so the run drawer shows a real tree
    and, on failure, a red step at 'Run tests')."""
    names = ["Set up job", "Checkout", "Setup Node", "Install dependencies", "Run tests", "Build"]
    if conclusion == "success":
        return _steps([(n, "success") for n in names])
    if conclusion == "failure":
        fail = "Run tests"
        pairs: list[tuple[str, str | None]] = []
        seen_fail = False
        for n in names:
            if n == fail:
                pairs.append((n, "failure"))
                seen_fail = True
            else:
                pairs.append((n, "skipped" if seen_fail else "success"))
        return _steps(pairs)
    # in_progress
    return _steps(
        [("Set up job", "success"), ("Checkout", "success"), ("Run tests", "in_progress")]
    )


def _job_payload(*, job_id, run_id, name, status, conclusion, steps, started, completed) -> dict:
    """A GitHub-shaped workflow_job object (with ``steps``) `normalize_workflow_job` can consume."""
    return {
        "id": job_id,
        "run_id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started,
        "completed_at": completed,
        "runner_name": "GitHub Actions 2",
        "runner_group_name": "GitHub Actions",
        "labels": ["ubuntu-latest"],
        "steps": steps,
    }


async def _seed_job(session, run_obj: dict, *, name: str, steps: list[dict]) -> None:
    """Persist one job (with its step tree) for a run. Skipped for queued runs (no job yet)."""
    if run_obj["status"] == "queued":
        return
    job = _job_payload(
        job_id=run_obj["id"] * 10 + 1,
        run_id=run_obj["id"],
        name=name,
        status=run_obj["status"],
        conclusion=run_obj["conclusion"],
        steps=steps,
        started=run_obj["run_started_at"],
        completed=run_obj["updated_at"] if run_obj["conclusion"] else None,
    )
    await upsert_job(session, events.normalize_workflow_job({"workflow_job": job}))


# Pipeline workflows (beyond ci.yml) so the Pipelines tab shows per-node status + a failing step.
# Each: (repo_id, path, wf_name, wf_id, run_id, status, conclusion, age_min, dur_s, job, steps)
# The headline demo: payments-api **Deploy** fails at `terraform apply`.
PIPELINE_RUNS = [
    (
        5001,
        ".github/workflows/deploy.yml",
        "Deploy",
        305001,
        790001,
        "completed",
        "failure",
        11,
        73,
        "deploy-production",
        [
            ("Set up job", "success"),
            ("Checkout", "success"),
            ("Configure AWS credentials", "success"),
            ("terraform init", "success"),
            ("terraform plan", "success"),
            ("terraform apply", "failure"),
            ("Slack notify", "skipped"),
        ],
    ),
    (
        5002,
        ".github/workflows/release.yml",
        "Release",
        305002,
        790002,
        "completed",
        "success",
        38,
        142,
        "release",
        [
            ("Set up job", "success"),
            ("Checkout", "success"),
            ("Build image", "success"),
            ("Push to registry", "success"),
            ("Trigger infra apply", "success"),
        ],
    ),
    (
        5003,
        ".github/workflows/apply.yml",
        "Apply",
        305003,
        790003,
        "in_progress",
        None,
        4,
        None,
        "terraform-apply",
        [
            ("Set up job", "success"),
            ("Checkout", "success"),
            ("terraform init", "success"),
            ("terraform apply", "in_progress"),
        ],
    ),
]


async def _ensure_sqlite_schema() -> None:
    """For the throwaway-sqlite path only, create the tables (Postgres uses alembic migrations)."""
    engine = get_engine()
    if engine.dialect.name == "sqlite":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


async def main() -> None:
    await _ensure_sqlite_schema()
    sessionmaker = get_sessionmaker()
    totals = {"runs": 0, "findings": 0, "jobs": 0}

    async with sessionmaker() as session:
        await upsert_installation(
            session,
            {
                "id": INSTALLATION_ID,
                "account_login": DEMO_ORG,
                "account_type": "Organization",
                "installed_at": datetime.now(UTC),
            },
        )
        template = await upsert_template(session, name="ci.yml", canonical_yaml=CANONICAL_CI)

        run_id = 700000
        for repo in REPOS:
            await upsert_repo(
                session,
                {
                    "id": repo["id"],
                    "owner": DEMO_ORG,
                    "name": repo["name"],
                    "default_branch": repo["branch"],
                    "archived": False,
                },
                installation_id=INSTALLATION_ID,
            )

            # One CI workflow per repo (explicit id so re-runs upsert via merge), so the run
            # detail drawer's metrics panel has a workflow to summarise.
            workflow_id = repo["id"] + 100000
            await session.merge(
                Workflow(id=workflow_id, repo_id=repo["id"], path=WORKFLOW_PATH, name="CI")
            )

            for shape in repo["runs"]:
                run_id += 1
                run_obj = _run_object(run_id, repo, shape, workflow_id)
                await upsert_run(session, events.normalize_run_object(run_obj, repo["id"]))
                totals["runs"] += 1
                # a job + step tree per run (so the run drawer shows exactly which step failed)
                await _seed_job(
                    session,
                    run_obj,
                    name="build",
                    steps=_ci_steps(run_obj["status"], run_obj["conclusion"]),
                )
                if run_obj["status"] != "queued":
                    totals["jobs"] += 1

            for finding_type, severity, ref, message in repo["findings"]:
                await upsert_finding(
                    session,
                    Finding(finding_type, severity, message, ref).as_row(
                        repo_id=repo["id"], path=WORKFLOW_PATH
                    ),
                )
                totals["findings"] += 1

            binding = await create_binding(
                session, repo_id=repo["id"], template_id=template.id, path=WORKFLOW_PATH
            )
            await update_binding_drift(session, binding, severity=repo["drift"])

        for repo_id, path, descriptor in RELATIONS:
            await upsert_workflow_relation(
                session, repo_id=repo_id, path=path, name=descriptor["name"], descriptor=descriptor
            )

        # Pipeline workflows + their latest run + a job/step tree, so each Pipelines node shows a
        # live status (and the Deploy node shows it failed at `terraform apply`).
        for (
            repo_id,
            path,
            wf_name,
            wf_id,
            rid,
            status,
            conclusion,
            age,
            dur,
            job_name,
            step_pairs,
        ) in PIPELINE_RUNS:
            await session.merge(Workflow(id=wf_id, repo_id=repo_id, path=path, name=wf_name))
            run_obj = _run_object(rid, {"branch": "main"}, (status, conclusion, age, dur), wf_id)
            await upsert_run(session, events.normalize_run_object(run_obj, repo_id))
            totals["runs"] += 1
            await _seed_job(session, run_obj, name=job_name, steps=_steps(step_pairs))
            totals["jobs"] += 1

        await session.commit()

    print(
        f"Seeded installation {INSTALLATION_ID} ({DEMO_ORG}): {len(REPOS)} repos, "
        f"{len(REPOS) + len(PIPELINE_RUNS)} workflows, {totals['runs']} runs, "
        f"{totals['jobs']} jobs (with step trees), {totals['findings']} findings, "
        f"{len(REPOS)} drift bindings, {len(RELATIONS)} workflow relations.\n"
        "Open the dashboard, or:  curl localhost:8000/api/v1/repos"
    )


if __name__ == "__main__":
    asyncio.run(main())
