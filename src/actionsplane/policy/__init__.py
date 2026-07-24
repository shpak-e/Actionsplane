"""Policy-readiness simulator (W2, flagship).

Evaluate a *proposed* org policy / ruleset against the fleet's already-analysed workflows and
answer the question GitHub's own insights API only gives you at GA: *"if I turn this on today, how
much breaks?"* — e.g. "this SHA-pin ruleset breaks 47 workflows in 23 repos" — and hand back the
repos a fix campaign would target.
"""

from actionsplane.policy.simulator import (
    Policy,
    RuleImpact,
    SimulationReport,
    WorkflowFacts,
    evaluate,
    simulate,
)

__all__ = [
    "Policy",
    "RuleImpact",
    "SimulationReport",
    "WorkflowFacts",
    "evaluate",
    "simulate",
]
