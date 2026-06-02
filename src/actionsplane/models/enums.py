"""Shared enumerations used across the audit, drift, and campaign engines."""

from __future__ import annotations

from enum import StrEnum


class PinState(StrEnum):
    """How an ``uses:`` reference is pinned. Ordered loosely worst -> best for risk."""

    UNPINNED = "unpinned"  # uses: actions/checkout  (no ref at all)
    BRANCH_PINNED = "branch"  # uses: actions/checkout@main
    TAG_PINNED = "tag"  # uses: actions/checkout@v4
    SHA_PINNED = "sha"  # uses: actions/checkout@<40-hex>
    LOCAL = "local"  # uses: ./.github/actions/foo  (same-repo, no pinning needed)
    DOCKER = "docker"  # uses: docker://alpine:3.20
    UNKNOWN_REF = "unknown"  # @stable, @release-2024 — could be mutable; assume worst


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingType(StrEnum):
    UNPINNED_ACTION = "unpinned_action"
    UNVERIFIED_PUBLISHER = "unverified_publisher"
    MISSING_PERMISSIONS = "missing_permissions"
    BROAD_PERMISSIONS = "broad_permissions"
    DEPRECATED_ACTION = "deprecated_action"
    DANGEROUS_SECRET_FLOW = "dangerous_secret_flow"
    MISSING_CONCURRENCY = "missing_concurrency"


class DriftSeverity(StrEnum):
    IDENTICAL = "identical"
    MINOR = "minor"  # whitespace / comment-only
    CONTENT_DRIFT = "content"  # values differ, structure same
    STRUCTURAL_DRIFT = "structural"  # jobs/steps added or removed


class CampaignStatus(StrEnum):
    PENDING = "pending"
    DRY_RUN_OK = "dry-run-ok"
    PR_OPENED = "pr-opened"
    PR_MERGED = "pr-merged"
    PR_CLOSED = "pr-closed"
    CONFLICT = "conflict"
    FAILED = "failed"
