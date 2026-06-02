"""Drift service (plan §5.3, Phase 3).

Glues the pure diff engine to real workflow content: parse a canonical template and a candidate
workflow, then diff them. ``compute_drift`` is the pure entry point (text in, report out) used by
both the CLI and the DB-backed binding checker.
"""

from __future__ import annotations

from actionsplane.audit.parser import parse_workflow
from actionsplane.drift.engine import DriftReport, diff


def compute_drift(
    canonical_yaml: str, candidate_yaml: str, *, path: str = "workflow.yml"
) -> DriftReport:
    """Parse both workflows and return the structural drift report."""
    canonical = parse_workflow(canonical_yaml, f"template::{path}")
    candidate = parse_workflow(candidate_yaml, path)
    return diff(canonical, candidate)


def autobind_paths(template_names: list[str], workflow_paths: list[str]) -> dict[str, str]:
    """Heuristic: bind a repo workflow to a template when basenames match.

    e.g. template "ci.yml" binds to ".github/workflows/ci.yml". Returns {path: template_name}.
    """
    by_base = {name: name for name in template_names}
    bindings: dict[str, str] = {}
    for path in workflow_paths:
        base = path.rsplit("/", 1)[-1]
        if base in by_base:
            bindings[path] = by_base[base]
    return bindings
