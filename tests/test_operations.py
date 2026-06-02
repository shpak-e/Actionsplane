"""Tests for bulk-edit operations (pin-shas)."""

from __future__ import annotations

from actionsplane.executor.operations import OPERATIONS, pin_workflow_to_sha

SHA_A = "a" * 40
SHA_B = "b" * 40

WF = """name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: org/action@main
      - uses: already/pinned@cccccccccccccccccccccccccccccccccccccccc
      - uses: ./.github/actions/local
      - run: make test
"""


def _resolver(table):
    return lambda owner, repo, ref: table.get((owner, repo, ref))


def test_pins_tag_and_branch_refs():
    table = {("actions", "checkout", "v4"): SHA_A, ("org", "action", "main"): SHA_B}
    res = pin_workflow_to_sha(WF, _resolver(table))
    assert f"actions/checkout@{SHA_A}" in res.new_text
    assert f"org/action@{SHA_B}" in res.new_text
    assert len(res.changes) == 2
    assert res.changed is True


def test_leaves_sha_local_and_run_untouched():
    res = pin_workflow_to_sha(WF, _resolver({}))  # resolver returns nothing
    # nothing resolvable -> no changes, text unchanged in substance
    assert res.changed is False
    assert "already/pinned@" + "c" * 40 in res.new_text
    assert "./.github/actions/local" in res.new_text


def test_tag_left_as_comment():
    table = {("actions", "checkout", "v4"): SHA_A, ("org", "action", "main"): SHA_B}
    res = pin_workflow_to_sha(WF, _resolver(table))
    assert "# v4" in res.new_text
    assert "# main" in res.new_text


def test_idempotent_second_run_is_noop():
    table = {("actions", "checkout", "v4"): SHA_A, ("org", "action", "main"): SHA_B}
    once = pin_workflow_to_sha(WF, _resolver(table))
    twice = pin_workflow_to_sha(once.new_text, _resolver(table))
    assert twice.changed is False  # everything already SHA-pinned


def test_operation_registry():
    assert "pin-shas" in OPERATIONS
    assert OPERATIONS["pin-shas"] is pin_workflow_to_sha
