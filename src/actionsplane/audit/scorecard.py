"""Org supply-chain posture scorecard (plan §13, research item #3).

A pure roll-up over *grouped counts* of open findings: severity counts and per-type breakdown,
plus a coarse 0-100 posture score weighted by severity. No I/O — the API GROUP-BYs open findings
in SQL and hands the counts here (so the score is exact at any volume, not a capped row slice;
review 3, P1.4). The score is intentionally simple and explainable, not a black box.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

# Score penalty per finding, by severity. Higher = worse posture.
_WEIGHT = {"critical": 25, "high": 10, "medium": 4, "low": 1, "info": 0}


@dataclass(frozen=True, slots=True)
class Scorecard:
    repos: int
    open_findings: int
    by_severity: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    score: int = 100  # 100 = clean; subtract weighted penalties, floored at 0


def build_scorecard(counts: Sequence[tuple[str, str, int]], *, repos: int) -> Scorecard:
    """Roll grouped open-finding counts ``(severity, finding_type, n)`` into a posture summary."""
    by_sev: Counter = Counter()
    by_type: Counter = Counter()
    for severity, finding_type, n in counts:
        by_sev[severity] += n
        by_type[finding_type] += n
    total = sum(by_sev.values())
    penalty = sum(_WEIGHT.get(sev, 0) * n for sev, n in by_sev.items())
    # normalise penalty per repo so a 200-repo org isn't unfairly crushed
    per_repo_penalty = penalty / repos if repos else penalty
    score = max(0, round(100 - per_repo_penalty))
    return Scorecard(
        repos=repos,
        open_findings=total,
        by_severity=dict(by_sev),
        by_type=dict(by_type),
        score=score,
    )
