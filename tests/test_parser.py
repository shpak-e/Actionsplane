"""Tests for the workflow AST parser."""

from __future__ import annotations

import pytest

from actionsplane.audit.parser import parse_workflow

CLEAN = """
name: CI
on: [push, pull_request]
permissions:
  contents: read
concurrency:
  group: ci-${{ github.ref }}
jobs:
  build:
    name: Build
    runs-on: ubuntu-latest
    needs: setup
    steps:
      - id: co
        uses: actions/checkout@8f4b7f84864484a7bf31766abe9204da3cbe65b3
      - name: test
        run: pytest
        env:
          CI: "true"
  setup:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/prep
"""


def test_parses_structure():
    wf = parse_workflow(CLEAN, "ci.yml")
    assert wf.name == "CI"
    assert set(wf.jobs) == {"build", "setup"}
    assert wf.jobs["build"].needs == ["setup"]
    assert wf.jobs["build"].runs_on == "ubuntu-latest"
    assert wf.permissions == {"contents": "read"}
    assert wf.concurrency is not None


def test_on_keyword_recovered():
    # YAML 1.1 turns `on:` into True; the parser must recover the triggers.
    wf = parse_workflow(CLEAN, "ci.yml")
    assert wf.on == ["push", "pull_request"]


def test_steps_and_uses():
    wf = parse_workflow(CLEAN, "ci.yml")
    steps = wf.jobs["build"].steps
    assert steps[0].uses.startswith("actions/checkout@")
    assert steps[0].id == "co"
    assert steps[1].run == "pytest"
    assert steps[1].env == {"CI": "true"}
    assert sorted(wf.all_uses()) == [
        "./.github/actions/prep",
        "actions/checkout@8f4b7f84864484a7bf31766abe9204da3cbe65b3",
    ]


def test_non_mapping_raises():
    with pytest.raises(ValueError):
        parse_workflow("- just\n- a\n- list\n", "bad.yml")
