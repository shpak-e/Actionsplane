"""Tests for the audit rule engine."""

from __future__ import annotations

from actionsplane.audit import (
    audit_concurrency,
    audit_deprecations,
    audit_permissions,
    audit_pins,
    audit_publisher_trust,
    audit_workflow,
    parse_workflow,
)
from actionsplane.models.enums import FindingType, Severity

SHA = "8f4b7f84864484a7bf31766abe9204da3cbe65b3"

CLEAN = f"""
name: ci
on: [push]
permissions:
  contents: read
concurrency:
  group: ci
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@{SHA}
"""

BAD = """
name: deploy
on: [push]
permissions: write-all
jobs:
  ship:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: tj-actions/changed-files@main
      - uses: actions/setup-node
      - run: echo "::set-output name=x::1"
"""


def _types(findings):
    return {f.finding_type for f in findings}


def test_clean_workflow_has_no_high_findings():
    wf = parse_workflow(CLEAN, "ci.yml")
    findings = audit_workflow(wf)
    assert all(f.severity != Severity.HIGH for f in findings)
    # SHA-pinned + has permissions + has concurrency -> no pin/perm/concurrency findings
    assert audit_pins(wf) == []
    assert audit_permissions(wf) == []
    assert audit_concurrency(wf) == []


def test_pins_flag_branch_and_tag_and_unpinned():
    wf = parse_workflow(BAD, "deploy.yml")
    pins = audit_pins(wf)
    refs = {f.ref for f in pins}
    assert "tj-actions/changed-files@main" in refs  # branch -> high
    assert "actions/checkout@v2" in refs  # tag -> medium
    assert "actions/setup-node" in refs  # unpinned -> high
    highs = {f.ref for f in pins if f.severity == Severity.HIGH}
    assert "tj-actions/changed-files@main" in highs
    assert "actions/setup-node" in highs


def test_permissions_broad_and_missing():
    wf = parse_workflow(BAD, "deploy.yml")
    perms = audit_permissions(wf)
    assert FindingType.BROAD_PERMISSIONS in _types(perms)  # write-all
    # missing-permissions should NOT fire because a (broad) block exists
    assert FindingType.MISSING_PERMISSIONS not in _types(perms)


def test_missing_permissions_when_absent():
    text = (
        "name: x\n"
        "on: [push]\n"
        "jobs:\n"
        "  a:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo hi\n"
    )
    wf = parse_workflow(text, "x.yml")
    assert FindingType.MISSING_PERMISSIONS in _types(audit_permissions(wf))


def test_deprecations():
    wf = parse_workflow(BAD, "deploy.yml")
    deps = audit_deprecations(wf)
    msgs = " ".join(f.message for f in deps)
    assert "actions/checkout@v2" in msgs  # deprecated major
    assert "::set-output" in msgs  # deprecated run command


def test_publisher_trust_allowlist():
    wf = parse_workflow(BAD, "deploy.yml")
    pubs = audit_publisher_trust(wf, allowlist={"actions"})
    owners = {f.ref for f in pubs}
    assert "tj-actions/changed-files@main" in owners  # tj-actions not allowlisted
    # actions/* is allowlisted -> not flagged
    assert not any("actions/checkout" in (r or "") for r in owners)


def test_concurrency_missing():
    wf = parse_workflow(BAD, "deploy.yml")
    assert FindingType.MISSING_CONCURRENCY in _types(audit_concurrency(wf))


def test_id_token_write_is_broad():
    # OIDC id-token:write enables cloud-credential minting — must be flagged broad.
    wf = parse_workflow(
        "name: x\non: [push]\npermissions:\n  id-token: write\n  contents: read\n"
        "jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
        "x.yml",
    )
    assert FindingType.BROAD_PERMISSIONS in _types(audit_permissions(wf))
