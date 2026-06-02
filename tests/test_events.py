"""Tests for webhook payload normalization (pure mapping functions)."""

from __future__ import annotations

from datetime import UTC, datetime

from actionsplane.ingestor import events

WORKFLOW_RUN = {
    "repository": {
        "id": 42,
        "name": "infra",
        "owner": {"login": "acme"},
        "default_branch": "main",
        "archived": False,
    },
    "installation": {"id": 999},
    "workflow_run": {
        "id": 1001,
        "workflow_id": 55,
        "run_number": 7,
        "head_branch": "feat/x",
        "head_sha": "abc123",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-05-24T10:00:00Z",
        "run_started_at": "2026-05-24T10:00:05Z",
        "updated_at": "2026-05-24T10:03:00Z",
        "run_attempt": 1,
        "actor": {"login": "octocat"},
    },
}

WORKFLOW_JOB = {
    "repository": {"id": 42, "name": "infra", "owner": {"login": "acme"}},
    "workflow_job": {
        "id": 2002,
        "run_id": 1001,
        "name": "build",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-05-24T10:00:10Z",
        "completed_at": "2026-05-24T10:02:50Z",
        "runner_name": "gh-hosted-1",
        "runner_group_name": "default",
        "labels": ["ubuntu-latest"],
    },
}


def test_normalize_repo():
    repo = events.normalize_repo(WORKFLOW_RUN)
    assert repo == {
        "id": 42,
        "owner": "acme",
        "name": "infra",
        "default_branch": "main",
        "archived": False,
    }


def test_normalize_workflow_run():
    row = events.normalize_workflow_run(WORKFLOW_RUN)
    assert row["id"] == 1001
    assert row["repo_id"] == 42
    assert row["workflow_id"] == 55
    assert row["conclusion"] == "success"
    assert row["actor"] == "octocat"
    assert row["created_at"] == datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)
    assert row["started_at"] == datetime(2026, 5, 24, 10, 0, 5, tzinfo=UTC)
    assert row["completed_at"] == datetime(2026, 5, 24, 10, 3, 0, tzinfo=UTC)
    assert row["raw_payload"]["id"] == 1001


def test_completed_at_none_when_in_progress():
    payload = {**WORKFLOW_RUN, "workflow_run": {**WORKFLOW_RUN["workflow_run"], "conclusion": None}}
    row = events.normalize_workflow_run(payload)
    assert row["completed_at"] is None


def test_normalize_workflow_job():
    row = events.normalize_workflow_job(WORKFLOW_JOB)
    assert row["id"] == 2002
    assert row["run_id"] == 1001
    assert row["runner_group"] == "default"
    assert row["labels"] == ["ubuntu-latest"]


def test_touches_workflows():
    push = {
        "commits": [
            {"added": ["src/x.py"], "modified": [".github/workflows/ci.yml"], "removed": []},
        ]
    }
    assert events.touches_workflows(push) is True
    no_wf = {"commits": [{"added": ["README.md"], "modified": [], "removed": []}]}
    assert events.touches_workflows(no_wf) is False
    assert events.touches_workflows({}) is False
