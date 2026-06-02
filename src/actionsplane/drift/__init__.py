"""Drift detection: structural diff of workflows against canonical templates."""

from actionsplane.drift.engine import DriftReport, diff
from actionsplane.drift.service import autobind_paths, compute_drift

__all__ = ["DriftReport", "autobind_paths", "compute_drift", "diff"]
