"""Tests for the posture scorecard and finding fingerprint."""

from __future__ import annotations

from actionsplane.audit.findings import Finding, fingerprint
from actionsplane.audit.scorecard import build_scorecard
from actionsplane.models.enums import FindingType, Severity


def test_fingerprint_is_stable_and_distinct():
    a = fingerprint(1, "ci.yml", "unpinned_action", "x@v1")
    b = fingerprint(1, "ci.yml", "unpinned_action", "x@v1")
    c = fingerprint(1, "ci.yml", "unpinned_action", "y@v1")
    assert a == b  # same logical finding -> same key (idempotent upsert)
    assert a != c  # different ref -> different key
    assert len(a) == 64  # sha256 hex


def test_finding_as_row_includes_fingerprint_and_path():
    f = Finding(FindingType.MISSING_PERMISSIONS, Severity.MEDIUM, "no perms")
    row = f.as_row(repo_id=7, path="release.yml")
    assert row["repo_id"] == 7
    assert row["path"] == "release.yml"
    assert row["fingerprint"] == fingerprint(7, "release.yml", "missing_permissions", None)


def test_scorecard_counts_and_score():
    # grouped counts: (severity, finding_type, n)
    counts = [
        ("high", "unpinned_action", 2),
        ("medium", "missing_permissions", 1),
    ]
    sc = build_scorecard(counts, repos=2)
    assert sc.open_findings == 3
    assert sc.by_severity == {"high": 2, "medium": 1}
    assert sc.by_type == {"unpinned_action": 2, "missing_permissions": 1}
    # penalty = 2*10 + 1*4 = 24; /2 repos = 12; 100 - 12 = 88
    assert sc.score == 88


def test_scorecard_clean_is_100():
    assert build_scorecard([], repos=5).score == 100


def test_scorecard_floors_at_zero():
    assert build_scorecard([("critical", "x", 100)], repos=1).score == 0
