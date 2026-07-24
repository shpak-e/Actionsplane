"""Dangling-reference diagnostics: missing workflow_run targets + missing reusable workflows."""

from __future__ import annotations

from actionsplane.audit.dangling import detect_dangling_references
from actionsplane.models.enums import FindingType


def _item(path, name, *, upstreams=(), calls=()):
    return {
        "path": path,
        "name": name,
        "descriptor": {"workflow_run_upstreams": list(upstreams), "calls": list(calls)},
    }


def test_workflow_run_to_existing_workflow_is_clean():
    items = [
        _item(".github/workflows/ci.yml", "CI"),
        _item(".github/workflows/deploy.yml", "Deploy", upstreams=["CI"]),
    ]
    assert detect_dangling_references(items) == []


def test_dangling_workflow_run_is_flagged():
    items = [
        _item(".github/workflows/ci.yml", "CI"),
        _item(".github/workflows/deploy.yml", "Deploy", upstreams=["Build"]),  # no "Build"
    ]
    out = detect_dangling_references(items)
    assert len(out) == 1
    path, finding = out[0]
    assert path == ".github/workflows/deploy.yml"
    assert finding.finding_type is FindingType.DANGLING_WORKFLOW_RUN
    assert finding.ref == "Build"


def test_local_reusable_call_to_existing_file_is_clean():
    items = [
        _item(".github/workflows/reusable.yml", "Reusable"),
        _item(
            ".github/workflows/main.yml",
            "Main",
            calls=[{"repo": None, "path": ".github/workflows/reusable.yml"}],
        ),
    ]
    assert detect_dangling_references(items) == []


def test_missing_local_reusable_workflow_is_flagged():
    items = [
        _item(
            ".github/workflows/main.yml",
            "Main",
            calls=[{"repo": None, "path": ".github/workflows/nope.yml"}],
        ),
    ]
    out = detect_dangling_references(items)
    assert len(out) == 1
    _, finding = out[0]
    assert finding.finding_type is FindingType.MISSING_REUSABLE_WORKFLOW
    assert finding.ref == ".github/workflows/nope.yml"


def test_cross_repo_reusable_call_is_ignored():
    # A reusable workflow in ANOTHER repo can't be verified from this repo's files → no finding.
    items = [
        _item(
            ".github/workflows/main.yml",
            "Main",
            calls=[{"repo": "org/other", "path": ".github/workflows/deploy.yml"}],
        ),
    ]
    assert detect_dangling_references(items) == []
