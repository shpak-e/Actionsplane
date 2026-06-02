"""Tests for the drift AST-diff engine."""

from __future__ import annotations

from actionsplane.audit.parser import parse_workflow
from actionsplane.drift import diff
from actionsplane.models.enums import DriftSeverity

CANON = """
name: CI
on: [push]
permissions:
  contents: read
concurrency:
  group: ci
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make test
"""


def _wf(text):
    return parse_workflow(text, "ci.yml")


def test_identical_when_only_comments_or_order_differ():
    # reordered top-level keys + a comment; structurally identical
    other = """
# a comment
on: [push]
name: CI
permissions:
  contents: read
concurrency:
  group: ci
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make test
"""
    report = diff(_wf(CANON), _wf(other))
    assert report.severity is DriftSeverity.IDENTICAL
    assert report.is_drifted is False


def test_name_only_change_is_minor():
    other = CANON.replace("name: CI", "name: Continuous Integration")
    report = diff(_wf(CANON), _wf(other))
    assert report.severity is DriftSeverity.MINOR
    assert report.is_drifted is False


def test_changed_action_version_is_content_drift():
    other = CANON.replace("actions/checkout@v4", "actions/checkout@v3")
    report = diff(_wf(CANON), _wf(other))
    assert report.severity is DriftSeverity.CONTENT_DRIFT
    assert report.is_drifted is True
    assert any("→" in c for c in report.changes)


def test_changed_permissions_is_content_drift():
    other = CANON.replace("contents: read", "contents: write")
    report = diff(_wf(CANON), _wf(other))
    assert report.severity is DriftSeverity.CONTENT_DRIFT


def test_added_step_is_structural():
    other = CANON.replace(
        "      - run: make test",
        "      - run: make test\n      - uses: actions/upload-artifact@v4",
    )
    report = diff(_wf(CANON), _wf(other))
    assert report.severity is DriftSeverity.STRUCTURAL_DRIFT
    assert report.is_drifted is True


def test_added_job_is_structural():
    other = (
        CANON
        + """
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: ./deploy.sh
"""
    )
    report = diff(_wf(CANON), _wf(other))
    assert report.severity is DriftSeverity.STRUCTURAL_DRIFT
    assert any("deploy" in c for c in report.changes)


def test_compute_drift_text_entrypoint():
    from actionsplane.drift import compute_drift

    canonical = CANON
    candidate = CANON.replace("actions/checkout@v4", "actions/checkout@v3")
    report = compute_drift(canonical, candidate, path="ci.yml")
    assert report.is_drifted is True
    assert report.severity.value == "content"


def test_autobind_matches_by_basename():
    from actionsplane.drift import autobind_paths

    result = autobind_paths(
        ["ci.yml", "release.yml"],
        [".github/workflows/ci.yml", ".github/workflows/deploy.yml"],
    )
    assert result == {".github/workflows/ci.yml": "ci.yml"}
