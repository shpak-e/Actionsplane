"""Cost/time optimization advice: pure heuristics over a workflow's aggregated cost metrics.

Turns the numbers the Cost view already computes (run count, failures, durations, queue time,
share of fleet compute) into a short list of concrete, ranked suggestions — "why is this workflow
expensive, and what would move the needle". Kept as a pure function so it's trivially unit-tested
and has no DB/network coupling; the API endpoint feeds it the per-workflow rollup.
"""

from __future__ import annotations

from dataclasses import dataclass

# Thresholds — deliberately conservative so a suggestion means something actionable, not noise.
_MIN_RUNS = 5  # below this the sample is too small to trust rates/percentiles
_QUEUE_WARN_S = 120  # p95 wait for a runner over ~2m is a real bottleneck
_FAILURE_WARN = 0.20  # ≥20% of runs failing is wasted compute worth flagging
_LONG_P95_S = 600  # p95 duration over ~10m is a caching/parallelism candidate
_FLEET_SHARE_WARN = 0.15  # a single workflow burning ≥15% of fleet minutes is the top lever


@dataclass(frozen=True, slots=True)
class CostSuggestion:
    kind: str  # queue | failures | duration | share
    severity: str  # info | warn
    message: str
    # An implemented bulk-edit operation that (partly) addresses this, when one exists.
    fix_operation: str | None = None


def _m(seconds: float) -> str:
    """Short human duration, e.g. 95 -> '1.6m', 600 -> '10m'."""
    minutes = seconds / 60
    return f"{minutes:.0f}m" if minutes >= 10 or minutes == int(minutes) else f"{minutes:.1f}m"


def cost_suggestions(
    *,
    runs: int,
    failures: int,
    total_minutes: float,
    p95_duration_s: float | None,
    p95_queue_s: float | None,
    fleet_minutes: float,
    has_concurrency: bool = True,
) -> list[CostSuggestion]:
    """Rank cost-reduction suggestions for one workflow. ``has_concurrency`` lets the caller mark
    workflows that already have a ``concurrency:`` block so the queue advice can offer the
    one-click add-concurrency fix only where it would actually apply."""
    out: list[CostSuggestion] = []

    # Share of fleet compute — the single biggest lever, so it leads.
    if fleet_minutes > 0 and total_minutes / fleet_minutes >= _FLEET_SHARE_WARN:
        pct = round(total_minutes / fleet_minutes * 100)
        out.append(
            CostSuggestion(
                "share",
                "info",
                f"Top consumer — {pct}% of fleet compute in this window. "
                "The biggest single lever for cutting Actions minutes.",
            )
        )

    # Failing runs waste minutes outright.
    if runs >= _MIN_RUNS and failures / runs >= _FAILURE_WARN:
        pct = round(failures / runs * 100)
        out.append(
            CostSuggestion(
                "failures",
                "warn",
                f"{pct}% of runs fail ({failures}/{runs}) — that compute is spent for nothing. "
                "Fix flaky or consistently-failing jobs before scaling this workflow up.",
            )
        )

    # Runner queue — capacity / concurrency problem, not a workflow-content one.
    if p95_queue_s is not None and p95_queue_s >= _QUEUE_WARN_S:
        msg = (
            f"Runs wait ~{_m(p95_queue_s)} for a runner (p95). Add runner capacity, use larger or "
            "self-hosted runners, "
        )
        if not has_concurrency:
            msg += "and add a concurrency group so a new commit cancels the superseded run."
            out.append(CostSuggestion("queue", "warn", msg, fix_operation="add-concurrency"))
        else:
            msg += "or reduce how often it triggers."
            out.append(CostSuggestion("queue", "warn", msg))

    # Long runs — caching / parallelism candidate.
    if p95_duration_s is not None and p95_duration_s >= _LONG_P95_S:
        out.append(
            CostSuggestion(
                "duration",
                "info",
                f"Long runs (p95 {_m(p95_duration_s)}). Cache dependencies (actions/cache), split "
                "or parallelize slow jobs with a matrix, and set timeout-minutes to cap runaways.",
            )
        )

    return out
