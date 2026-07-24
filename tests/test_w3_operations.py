"""W3 supporting edit ops: set-permissions, swap-runs-on, bump-action-version."""

from __future__ import annotations

from actionsplane.executor.operations import (
    OPERATIONS,
    add_concurrency,
    bump_action_version,
    set_workflow_permissions,
    swap_runs_on,
    sync_to_template,
)

WF_NO_PERMS = """name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/setup-node@v2
      - run: make test
"""

WF_LIST_RUNNER = """name: ci
on: [push]
jobs:
  a:
    runs-on: [self-hosted, ubuntu-22.04]
    steps:
      - run: echo hi
  b:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""


def test_registry_has_all_w3_ops():
    for name in ("set-permissions", "swap-runs-on", "bump-action-version", "add-concurrency"):
        assert name in OPERATIONS


# --- set-permissions --------------------------------------------------------------------------
def test_set_permissions_adds_least_privilege_after_on():
    res = set_workflow_permissions(WF_NO_PERMS)
    assert res.changed is True
    assert "permissions:" in res.new_text
    assert "contents: read" in res.new_text
    # inserted right after `on:`, before `jobs:`
    assert res.new_text.index("permissions:") < res.new_text.index("jobs:")


def test_set_permissions_replaces_broad_grant():
    wf = WF_NO_PERMS.replace("on: [push]\n", "on: [push]\npermissions: write-all\n")
    res = set_workflow_permissions(wf)
    assert res.changed is True
    assert "write-all" not in res.new_text
    assert "contents: read" in res.new_text


def test_set_permissions_custom_params():
    res = set_workflow_permissions(
        WF_NO_PERMS, params={"permissions": {"contents": "read", "id-token": "write"}}
    )
    assert "id-token: write" in res.new_text


def test_set_permissions_idempotent():
    once = set_workflow_permissions(WF_NO_PERMS)
    twice = set_workflow_permissions(once.new_text)
    assert twice.changed is False


# --- swap-runs-on -----------------------------------------------------------------------------
def test_swap_runs_on_scalar_and_list():
    res = swap_runs_on(WF_LIST_RUNNER)  # default ubuntu-22.04 -> ubuntu-24.04
    assert res.changed is True
    assert "ubuntu-24.04" in res.new_text
    assert "ubuntu-22.04" not in res.new_text
    assert "ubuntu-latest" in res.new_text  # untouched
    assert len(res.changes) == 1  # only job `a`'s list label matched


def test_swap_runs_on_custom_and_idempotent():
    res = swap_runs_on(WF_NO_PERMS, params={"from": "ubuntu-22.04", "to": "ubuntu-24.04"})
    assert "runs-on: ubuntu-24.04" in res.new_text
    assert swap_runs_on(res.new_text).changed is False


# --- bump-action-version ----------------------------------------------------------------------
def test_bump_action_version_bumps_matching_action():
    res = bump_action_version(WF_NO_PERMS, params={"action": "actions/setup-node", "to": "v4"})
    assert res.changed is True
    assert "actions/setup-node@v4" in res.new_text
    assert "@v2" not in res.new_text


def test_bump_action_version_requires_params():
    assert bump_action_version(WF_NO_PERMS).changed is False  # no params -> no-op


def test_bump_action_version_ignores_other_actions():
    res = bump_action_version(WF_NO_PERMS, params={"action": "actions/checkout", "to": "v4"})
    assert res.changed is False  # no actions/checkout in this workflow


# --- add-concurrency --------------------------------------------------------------------------
def test_add_concurrency_inserts_default_block():
    res = add_concurrency(WF_NO_PERMS)
    assert res.changed is True
    assert "concurrency:" in res.new_text
    assert "cancel-in-progress: true" in res.new_text
    assert "${{ github.workflow }}-${{ github.ref }}" in res.new_text
    # inserted after `on:`, before `jobs:`
    assert res.new_text.index("concurrency:") < res.new_text.index("jobs:")


def test_add_concurrency_idempotent():
    once = add_concurrency(WF_NO_PERMS)
    twice = add_concurrency(once.new_text)
    assert twice.changed is False  # a concurrency block already exists


def test_add_concurrency_custom_params():
    res = add_concurrency(WF_NO_PERMS, params={"group": "deploy", "cancel_in_progress": False})
    assert "group: deploy" in res.new_text
    assert "cancel-in-progress: false" in res.new_text


# --- sync-template ----------------------------------------------------------------------------
CANONICAL = """name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - run: make ci
"""


def test_sync_template_replaces_with_canonical():
    res = sync_to_template(WF_NO_PERMS, params={"canonical_yaml": CANONICAL})
    assert res.changed is True
    assert res.new_text == CANONICAL
    assert res.changes == ["sync workflow to canonical template"]


def test_sync_template_noop_when_already_matching():
    res = sync_to_template(CANONICAL, params={"canonical_yaml": CANONICAL})
    assert res.changed is False


def test_sync_template_noop_without_canonical():
    assert sync_to_template(WF_NO_PERMS).changed is False
    assert "sync-template" in OPERATIONS
