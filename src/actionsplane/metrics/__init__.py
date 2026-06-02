"""Metrics computation: pure functions over run/job records (plan §5.5)."""

from actionsplane.metrics.compute import (
    WorkflowMetrics,
    flake_rate,
    percentile,
    success_rate,
    summarize_runs,
)

__all__ = [
    "WorkflowMetrics",
    "flake_rate",
    "percentile",
    "success_rate",
    "summarize_runs",
]
