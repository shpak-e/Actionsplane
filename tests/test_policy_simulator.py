"""W2 — policy-readiness simulator: pure evaluation + the DB fact-gathering service."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from actionsplane.audit.findings import Finding
from actionsplane.db.base import Base
from actionsplane.db.models import Installation, Repo
from actionsplane.db.repository import upsert_finding, upsert_workflow_relation
from actionsplane.models.enums import FindingType, Severity
from actionsplane.policy import Policy, WorkflowFacts, evaluate, simulate
from actionsplane.policy.service import simulate_policy


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element, compiler, **kw):
    return "JSON"


def _facts(**kw) -> WorkflowFacts:
    base = dict(repo_id=1, repo_full="acme/a", path=".github/workflows/ci.yml", name="ci")
    return WorkflowFacts(**{**base, **kw})


def test_evaluate_sha_pin_rule():
    policy = Policy(require_sha_pinned=True)
    assert evaluate(policy, _facts(has_unpinned_action=True))[0].rule == "require_sha_pinned"
    assert evaluate(policy, _facts(has_unpinned_action=False)) == []


def test_evaluate_disallowed_trigger_rule():
    policy = Policy(disallowed_triggers=("pull_request_target", "workflow_dispatch"))
    v = evaluate(policy, _facts(triggers=("push", "pull_request_target")))
    assert v[0].rule == "disallowed_trigger"
    assert "pull_request_target" in v[0].detail
    assert evaluate(policy, _facts(triggers=("push",))) == []


def test_simulate_aggregates_and_flags_fixable_repos():
    policy = Policy(require_sha_pinned=True, disallowed_triggers=("pull_request_target",))
    fleet = [
        _facts(repo_id=1, repo_full="acme/a", has_unpinned_action=True),
        _facts(repo_id=2, repo_full="acme/b", triggers=("pull_request_target",)),
        _facts(repo_id=3, repo_full="acme/c", has_unpinned_action=True, triggers=("push",)),
        _facts(repo_id=4, repo_full="acme/d"),  # compliant
    ]
    report = simulate(policy, fleet)
    assert report.workflows_evaluated == 4
    assert report.workflows_violating == 3
    assert report.repos_violating == 3
    by_rule = {r.rule: r for r in report.by_rule}
    assert by_rule["require_sha_pinned"].workflows == 2
    assert by_rule["require_sha_pinned"].fix_operation == "pin-shas"
    assert by_rule["require_sha_pinned"].fixable_repo_ids == [1, 3]
    # A rule with no automatic fix exposes no repo ids for a campaign.
    assert by_rule["disallowed_trigger"].fix_operation is None
    assert by_rule["disallowed_trigger"].fixable_repo_ids == []


def test_simulate_sample_limit():
    policy = Policy(require_sha_pinned=True)
    fleet = [_facts(repo_id=i, has_unpinned_action=True) for i in range(50)]
    report = simulate(policy, fleet, sample_limit=10)
    assert report.workflows_violating == 50
    assert len(report.samples) == 10


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(
            Installation(
                id=1, account_login="acme", account_type="User", installed_at=datetime.now(UTC)
            )
        )
        s.add(Repo(id=10, installation_id=1, owner="acme", name="messy"))
        s.add(Repo(id=11, installation_id=1, owner="acme", name="clean"))
        await s.commit()
        yield s
    await engine.dispose()


async def _finding(session, repo_id, ftype, path):
    row = Finding(ftype, Severity.MEDIUM, "msg").as_row(repo_id=repo_id, path=path)
    row["first_seen_at"] = row["last_seen_at"] = datetime.now(UTC)
    await upsert_finding(session, row)


async def test_simulate_endpoint_maps_report_to_schema(session):
    """The endpoint wrapper maps the request schema -> Policy and the dataclass report -> schema."""
    from actionsplane.api.app import simulate_policy_endpoint
    from actionsplane.api.schemas import PolicySimulateIn

    await upsert_workflow_relation(
        session,
        repo_id=10,
        path=".github/workflows/ci.yml",
        name="ci",
        descriptor={"triggers": ["push"]},
    )
    await _finding(session, 10, FindingType.UNPINNED_ACTION, ".github/workflows/ci.yml")
    await session.commit()

    out = await simulate_policy_endpoint(PolicySimulateIn(require_sha_pinned=True), session)
    assert out.workflows_violating == 1
    assert out.by_rule[0].rule == "require_sha_pinned"
    assert out.by_rule[0].fixable_repo_ids == [10]


async def test_simulate_policy_from_stored_fleet(session):
    # messy: has an unpinned finding + a pull_request_target trigger; clean: neither.
    await upsert_workflow_relation(
        session,
        repo_id=10,
        path=".github/workflows/ci.yml",
        name="ci",
        descriptor={"triggers": ["push", "pull_request_target"]},
    )
    await upsert_workflow_relation(
        session,
        repo_id=11,
        path=".github/workflows/ci.yml",
        name="ci",
        descriptor={"triggers": ["push"]},
    )
    await _finding(session, 10, FindingType.UNPINNED_ACTION, ".github/workflows/ci.yml")
    await session.commit()

    report = await simulate_policy(
        session, Policy(require_sha_pinned=True, disallowed_triggers=("pull_request_target",))
    )
    assert report.workflows_evaluated == 2
    assert report.repos_violating == 1  # only the messy repo
    by_rule = {r.rule: r for r in report.by_rule}
    assert by_rule["require_sha_pinned"].fixable_repo_ids == [10]
    assert by_rule["disallowed_trigger"].workflows == 1
    assert report.samples[0]["repo"] == "acme/messy"
