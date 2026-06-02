"""Tests for `actionsplane audit local` — scanning a local repo's workflow files."""

from __future__ import annotations

import pathlib

from typer.testing import CliRunner

from actionsplane.cli.main import app

runner = CliRunner()

# Tag-pinned action + no permissions block → guaranteed findings.
UNPINNED = """name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
"""


def _repo(tmp_path: pathlib.Path, content: str) -> pathlib.Path:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(content, encoding="utf-8")
    return tmp_path


def test_local_audit_flags_findings_and_exits_nonzero(tmp_path):
    result = runner.invoke(app, ["audit", "local", str(_repo(tmp_path, UNPINNED))])
    assert result.exit_code == 1
    assert "finding" in result.stdout.lower()


def test_local_audit_exit_zero_flag(tmp_path):
    result = runner.invoke(app, ["audit", "local", str(_repo(tmp_path, UNPINNED)), "--exit-zero"])
    assert result.exit_code == 0


def test_local_audit_no_workflows(tmp_path):
    result = runner.invoke(app, ["audit", "local", str(tmp_path)])
    assert result.exit_code == 1
    assert "no workflow files" in result.stdout.lower()
