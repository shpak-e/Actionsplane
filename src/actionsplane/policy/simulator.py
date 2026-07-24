"""Pure policy-simulation core (W2).

No I/O: given a :class:`Policy` and the :class:`WorkflowFacts` the service gathered from stored
audit findings + relation descriptors, compute which workflows would violate the policy and
aggregate that into a :class:`SimulationReport`. Keeping it pure means the whole "what would this
ruleset break?" evaluation is unit-testable over fixtures and runs over a whole fleet in-process.

The rules model the enforcement knobs GitHub shipped in the 2026-06 execution-protections preview:
require SHA-pinned actions, restrict which trigger events are allowed, and require least-privilege
`permissions:`. Each rule that ActionsPlane can remediate carries the operation a fix campaign
would run, so the report is directly actionable ("→ one click → fix campaign").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# Rule id -> the campaign operation that remediates it (None = no automatic fix today).
_FIX_OPERATION: dict[str, str | None] = {
    "require_sha_pinned": "pin-shas",
    "disallowed_trigger": None,  # remediation is workflow-specific (drop/guard the trigger)
    "require_permissions": None,  # needs a per-workflow least-privilege set
}


@dataclass(frozen=True, slots=True)
class Policy:
    """A proposed org policy to simulate against the fleet.

    ``disallowed_triggers`` models execution-protection event rules — e.g. forbidding
    ``pull_request_target`` (the classic privileged-context foot-gun) or ``workflow_dispatch``.
    """

    require_sha_pinned: bool = False
    disallowed_triggers: tuple[str, ...] = ()
    require_permissions: bool = False

    @property
    def active_rules(self) -> tuple[str, ...]:
        rules: list[str] = []
        if self.require_sha_pinned:
            rules.append("require_sha_pinned")
        if self.disallowed_triggers:
            rules.append("disallowed_trigger")
        if self.require_permissions:
            rules.append("require_permissions")
        return tuple(rules)


@dataclass(frozen=True, slots=True)
class WorkflowFacts:
    """What the service knows about one workflow from stored analysis (no re-fetch needed)."""

    repo_id: int
    repo_full: str  # "owner/name"
    path: str
    name: str | None
    triggers: tuple[str, ...] = ()
    has_unpinned_action: bool = False  # ≥1 open unpinned_action finding
    missing_permissions: bool = False  # ≥1 open missing_permissions finding


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str
    detail: str


@dataclass(frozen=True, slots=True)
class RuleImpact:
    """Per-rule rollup: how much this one rule breaks, and how to fix it."""

    rule: str
    workflows: int
    repos: int
    fix_operation: str | None
    fixable_repo_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SimulationReport:
    policy_rules: tuple[str, ...]
    workflows_evaluated: int
    workflows_violating: int
    repos_violating: int
    by_rule: list[RuleImpact]
    samples: list[dict]  # up to N example violations for the UI


def evaluate(policy: Policy, facts: WorkflowFacts) -> list[Violation]:
    """Return every way ``facts`` violates ``policy`` (empty = compliant)."""
    out: list[Violation] = []
    if policy.require_sha_pinned and facts.has_unpinned_action:
        out.append(Violation("require_sha_pinned", "uses a mutable (non-SHA) action ref"))
    if policy.disallowed_triggers:
        hit = sorted(set(facts.triggers) & set(policy.disallowed_triggers))
        if hit:
            out.append(Violation("disallowed_trigger", f"triggered by {', '.join(hit)}"))
    if policy.require_permissions and facts.missing_permissions:
        out.append(Violation("require_permissions", "no workflow-level permissions: block"))
    return out


def simulate(
    policy: Policy, fleet: list[WorkflowFacts], *, sample_limit: int = 25
) -> SimulationReport:
    """Evaluate ``policy`` across the whole fleet and roll the violations up for the UI."""
    violating_workflows = 0
    violating_repos: set[int] = set()
    per_rule_workflows: Counter[str] = Counter()
    per_rule_repos: dict[str, set[int]] = {r: set() for r in policy.active_rules}
    samples: list[dict] = []

    for f in fleet:
        violations = evaluate(policy, f)
        if not violations:
            continue
        violating_workflows += 1
        violating_repos.add(f.repo_id)
        for v in violations:
            per_rule_workflows[v.rule] += 1
            per_rule_repos.setdefault(v.rule, set()).add(f.repo_id)
        if len(samples) < sample_limit:
            samples.append(
                {
                    "repo": f.repo_full,
                    "path": f.path,
                    "rules": [v.rule for v in violations],
                    "details": [v.detail for v in violations],
                }
            )

    by_rule = [
        RuleImpact(
            rule=rule,
            workflows=per_rule_workflows.get(rule, 0),
            repos=len(per_rule_repos.get(rule, set())),
            fix_operation=_FIX_OPERATION.get(rule),
            # Only rules with an automatic fix expose repo ids for a one-click campaign.
            fixable_repo_ids=(
                sorted(per_rule_repos.get(rule, set())) if _FIX_OPERATION.get(rule) else []
            ),
        )
        for rule in policy.active_rules
    ]

    return SimulationReport(
        policy_rules=policy.active_rules,
        workflows_evaluated=len(fleet),
        workflows_violating=violating_workflows,
        repos_violating=len(violating_repos),
        by_rule=by_rule,
        samples=samples,
    )
