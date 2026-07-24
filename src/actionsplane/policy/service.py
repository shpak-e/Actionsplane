"""Gather fleet facts from stored analysis and run a policy simulation (W2).

No GitHub I/O: the workflow universe is the ``workflow_relations`` rows (one per analysed
workflow, carrying its ``triggers``), and violation signals come from the already-stored open
findings (unpinned actions, missing permissions). So a simulation is a couple of indexed reads —
fast enough to re-run live as the operator tweaks the proposed policy in the UI.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.db.models import AuditFinding, Repo, WorkflowRelation
from actionsplane.models.enums import FindingType
from actionsplane.policy.simulator import Policy, SimulationReport, WorkflowFacts, simulate

# Finding types that feed a policy rule's violation signal.
_SIGNAL_TYPES = (FindingType.UNPINNED_ACTION.value, FindingType.MISSING_PERMISSIONS.value)


async def gather_fleet_facts(session: AsyncSession) -> list[WorkflowFacts]:
    """Build one WorkflowFacts per analysed workflow from relations + open findings."""
    repo_full = {r.id: f"{r.owner}/{r.name}" for r in (await session.scalars(select(Repo))).all()}

    # (repo_id, path) -> set of finding types open on it, for the types we care about.
    signals: dict[tuple[int, str | None], set[str]] = {}
    rows = await session.execute(
        select(AuditFinding.repo_id, AuditFinding.path, AuditFinding.finding_type).where(
            AuditFinding.resolved_at.is_(None),
            AuditFinding.finding_type.in_(_SIGNAL_TYPES),
        )
    )
    for repo_id, path, ftype in rows:
        signals.setdefault((repo_id, path), set()).add(ftype)

    facts: list[WorkflowFacts] = []
    for rel in await session.scalars(select(WorkflowRelation)):
        desc = rel.descriptor or {}
        sig = signals.get((rel.repo_id, rel.path), set())
        facts.append(
            WorkflowFacts(
                repo_id=rel.repo_id,
                repo_full=repo_full.get(rel.repo_id, str(rel.repo_id)),
                path=rel.path,
                name=rel.name,
                triggers=tuple(desc.get("triggers", ())),
                has_unpinned_action=FindingType.UNPINNED_ACTION.value in sig,
                missing_permissions=FindingType.MISSING_PERMISSIONS.value in sig,
            )
        )
    return facts


async def simulate_policy(session: AsyncSession, policy: Policy) -> SimulationReport:
    """Evaluate ``policy`` against the current fleet analysis."""
    return simulate(policy, await gather_fleet_facts(session))
