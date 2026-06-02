"""Audit engine — analyse workflows for supply-chain and hygiene problems (plan §5.2).

Phase 2: pin, permission, deprecation, publisher-trust, and concurrency audits over a parsed
workflow AST. ``classify`` (pin classifier) and ``parse_workflow`` are the building blocks.
"""

from actionsplane.audit.engine import (
    audit_concurrency,
    audit_deprecations,
    audit_permissions,
    audit_pins,
    audit_publisher_trust,
    audit_workflow,
)
from actionsplane.audit.findings import Finding
from actionsplane.audit.parser import parse_workflow
from actionsplane.audit.pins import UsesRef, classify, is_pinned_safely

__all__ = [
    "Finding",
    "UsesRef",
    "audit_concurrency",
    "audit_deprecations",
    "audit_permissions",
    "audit_pins",
    "audit_publisher_trust",
    "audit_workflow",
    "classify",
    "is_pinned_safely",
    "parse_workflow",
]
