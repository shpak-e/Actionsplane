"""Metrics computation (plan §5.5).

Pure functions over plain run records (dicts/dataclasses), deliberately decoupled from the
ORM so they unit-test without a database and can run over either live rows or materialised-
view rows. The API and worker fetch rows and pass them here.

A "run record" only needs these keys:
    conclusion   : str | None   ("success" | "failure" | "cancelled" | ...)
    head_sha     : str | None
    duration_s   : float | None (completed_at - started_at)
    queue_s      : float | None (started_at - created_at)
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

RunRecord = dict[str, Any]

_SUCCESS = "success"
_FAILURE = "failure"


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolation percentile (p in [0, 100]). Returns None for empty input."""
    if not values:
        return None
    if not 0 <= p <= 100:
        raise ValueError("p must be in [0, 100]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (p / 100) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[low])
    frac = rank - low
    return float(ordered[low] * (1 - frac) + ordered[high] * frac)


def success_rate(runs: Sequence[RunRecord]) -> float | None:
    """Fraction of *concluded* runs that succeeded. None if no concluded runs."""
    concluded = [r for r in runs if r.get("conclusion") in (_SUCCESS, _FAILURE)]
    if not concluded:
        return None
    successes = sum(1 for r in concluded if r["conclusion"] == _SUCCESS)
    return successes / len(concluded)


def flake_rate(runs: Sequence[RunRecord]) -> float | None:
    """Flake rate: fraction of commit SHAs that both failed and succeeded.

    A flake is a SHA that produced at least one failure *and* at least one success — i.e.
    the same code passed and failed, so the failure was non-deterministic. None if no SHAs.
    """
    by_sha: dict[str, set[str]] = defaultdict(set)
    for r in runs:
        sha = r.get("head_sha")
        conclusion = r.get("conclusion")
        if sha and conclusion in (_SUCCESS, _FAILURE):
            by_sha[sha].add(conclusion)
    if not by_sha:
        return None
    flaky = sum(1 for outcomes in by_sha.values() if outcomes == {_SUCCESS, _FAILURE})
    return flaky / len(by_sha)


@dataclass(frozen=True, slots=True)
class WorkflowMetrics:
    runs: int
    successes: int
    failures: int
    success_rate: float | None
    p50_duration_s: float | None
    p95_duration_s: float | None
    p95_queue_s: float | None
    flake_rate: float | None


def summarize_runs(runs: Sequence[RunRecord]) -> WorkflowMetrics:
    """Roll a set of run records up into a single metrics summary."""
    durations = [r["duration_s"] for r in runs if r.get("duration_s") is not None]
    queues = [r["queue_s"] for r in runs if r.get("queue_s") is not None]
    successes = sum(1 for r in runs if r.get("conclusion") == _SUCCESS)
    failures = sum(1 for r in runs if r.get("conclusion") == _FAILURE)
    return WorkflowMetrics(
        runs=len(runs),
        successes=successes,
        failures=failures,
        success_rate=success_rate(runs),
        p50_duration_s=percentile(durations, 50),
        p95_duration_s=percentile(durations, 95),
        p95_queue_s=percentile(queues, 95),
        flake_rate=flake_rate(runs),
    )
