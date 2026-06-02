"""Org supply-chain posture scorecard (plan §13, research item #3).

A pure roll-up over a set of open findings: severity counts and per-type breakdown, plus a
coarse 0-100 posture score weighted by severity. No I/O — the API fetches open findings and
hands them here. The score is intentionally simple and explainable, not a black box.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

# Score penalty per finding, by severity. Higher = worse posture.
_WEIGHT = {"critical": 25, "high": 10, "medium": 4, "low": 1, "info": 0}


@dataclass(frozen=True, slots=True)
class Scorecard:
    repos: int
    open_findings: int
    by_severity: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    score: int = 100  # 100 = clean; subtract weighted penalties, floored at 0


def build_scorecard(findings: Sequence[Any], *, repos: int) -> Scorecard:
    """Roll a list of open-finding records (objects or dicts) into a posture summary."""

    def attr(f: Any, name: str) -> str:
        return f.get(name) if isinstance(f, dict) else getattr(f, name)

    by_sev = Counter(attr(f, "severity") for f in findings)
    by_type = Counter(attr(f, "finding_type") for f in findings)
    penalty = sum(_WEIGHT.get(sev, 0) * n for sev, n in by_sev.items())
    # normalise penalty per repo so a 200-repo org isn't unfairly crushed
    per_repo_penalty = penalty / repos if repos else penalty
    score = max(0, round(100 - per_repo_penalty))
    return Scorecard(
        repos=repos,
        open_findings=len(findings),
        by_severity=dict(by_sev),
        by_type=dict(by_type),
        score=score,
    )
