"""Audit rule engine (plan §5.2).

Runs a set of pure rules over a parsed ``Workflow`` and returns ``Finding`` objects. No I/O, no
DB — so the whole suite is unit-testable in-process and can fan out over an org's workflows
cheaply. Each rule is a small function; ``audit_workflow`` composes them.
"""

from __future__ import annotations

import re

from actionsplane.audit.deprecations import DEPRECATED_MAJORS, DEPRECATED_RUN_TOKENS
from actionsplane.audit.findings import Finding
from actionsplane.audit.pins import classify
from actionsplane.models.enums import FindingType, PinState, Severity
from actionsplane.models.workflow import Workflow

_MAJOR_RE = re.compile(r"^(v?\d+)")


def audit_pins(wf: Workflow, *, immutable_refs: frozenset[str] | None = None) -> list[Finding]:
    """Flag every non-SHA-pinned action reference (mutable refs are the attack surface).

    A tag proven to be a GitHub immutable release (``immutable_refs``) is treated as safe and not
    flagged — it's tamper-proof like a SHA but still updatable, so nagging to pin it to a raw SHA
    would be a regression in maintainability for no security gain.
    """
    findings: list[Finding] = []
    for ref in wf.all_uses():
        u = classify(ref, immutable_refs=immutable_refs)
        if u.pin_state in (PinState.UNPINNED, PinState.BRANCH_PINNED, PinState.UNKNOWN_REF):
            findings.append(
                Finding(
                    FindingType.UNPINNED_ACTION,
                    Severity.HIGH,
                    f"`{ref}` is {u.pin_state.value}-pinned; pin to a full commit SHA.",
                    ref=ref,
                )
            )
        elif u.pin_state is PinState.TAG_PINNED:
            findings.append(
                Finding(
                    FindingType.UNPINNED_ACTION,
                    Severity.MEDIUM,
                    f"`{ref}` is tag-pinned; tags are mutable — pin to a full commit SHA.",
                    ref=ref,
                )
            )
    return findings


def audit_permissions(wf: Workflow) -> list[Finding]:
    """Flag missing or over-broad ``permissions:`` blocks (least-privilege for GITHUB_TOKEN)."""
    findings: list[Finding] = []

    # Scopes whose `write` grant is high-impact: code push, OIDC cred minting, package/release
    # and workflow modification. id-token:write enables cloud-credential theft via OIDC.
    sensitive_write = ("contents", "id-token", "packages", "actions", "deployments")

    def is_broad(perms: object) -> bool:
        if perms == "write-all":
            return True
        if isinstance(perms, dict):
            return any(perms.get(scope) == "write" for scope in sensitive_write)
        return False

    # Missing-permissions is about the *default* token scope, which is set at the workflow
    # level. A workflow with no top-level block leaves jobs that don't override it on the
    # broad default — so flag whenever the top-level block is absent.
    if wf.permissions is None:
        findings.append(
            Finding(
                FindingType.MISSING_PERMISSIONS,
                Severity.MEDIUM,
                "No workflow-level `permissions:` block; GITHUB_TOKEN falls back to the repo "
                "default (often write-all). Set least-privilege permissions at the top level.",
            )
        )
    if is_broad(wf.permissions):
        findings.append(
            Finding(
                FindingType.BROAD_PERMISSIONS,
                Severity.HIGH,
                "Workflow-level `permissions` grant `contents: write` (or write-all); "
                "scope down to what the jobs need.",
            )
        )
    for jid, job in wf.jobs.items():
        if is_broad(job.permissions):
            findings.append(
                Finding(
                    FindingType.BROAD_PERMISSIONS,
                    Severity.MEDIUM,
                    f"Job `{jid}` grants broad write permissions; scope down.",
                    ref=jid,
                )
            )
    return findings


def _major(ref: str | None) -> str | None:
    if not ref:
        return None
    m = _MAJOR_RE.match(ref)
    return m.group(1) if m else None


def audit_deprecations(wf: Workflow) -> list[Finding]:
    """Flag deprecated action majors and deprecated workflow commands in run scripts."""
    findings: list[Finding] = []
    for ref in wf.all_uses():
        u = classify(ref)
        major = _major(u.ref)
        if u.action and major and major in DEPRECATED_MAJORS.get(u.action, set()):
            findings.append(
                Finding(
                    FindingType.DEPRECATED_ACTION,
                    Severity.MEDIUM,
                    f"`{u.action}@{major}` is deprecated (old runtime); "
                    "upgrade to the latest major.",
                    ref=ref,
                )
            )
    for job in wf.jobs.values():
        for step in job.steps:
            if not step.run:
                continue
            for token in DEPRECATED_RUN_TOKENS:
                if token in step.run:
                    findings.append(
                        Finding(
                            FindingType.DEPRECATED_ACTION,
                            Severity.MEDIUM,
                            f"Deprecated workflow command `{token}` used in a run step.",
                            ref=step.id or step.name,
                        )
                    )
    return findings


def audit_publisher_trust(wf: Workflow, allowlist: set[str]) -> list[Finding]:
    """Flag actions whose owner is not in the org allowlist (skips local/docker refs)."""
    findings: list[Finding] = []
    for ref in wf.all_uses():
        u = classify(ref)
        if u.owner and u.owner not in allowlist:
            findings.append(
                Finding(
                    FindingType.UNVERIFIED_PUBLISHER,
                    Severity.MEDIUM,
                    f"Action publisher `{u.owner}` is not in the allowlist.",
                    ref=ref,
                )
            )
    return findings


def audit_concurrency(wf: Workflow) -> list[Finding]:
    """Flag workflows without a `concurrency:` block (deploys can race)."""
    if wf.concurrency is None:
        return [
            Finding(
                FindingType.MISSING_CONCURRENCY,
                Severity.LOW,
                "No `concurrency:` block; concurrent runs on the same ref can race.",
            )
        ]
    return []


def audit_workflow(
    wf: Workflow,
    *,
    publisher_allowlist: set[str] | None = None,
    immutable_refs: frozenset[str] | None = None,
) -> list[Finding]:
    """Run the full audit suite over one workflow and return all findings."""
    findings = [
        *audit_pins(wf, immutable_refs=immutable_refs),
        *audit_permissions(wf),
        *audit_deprecations(wf),
        *audit_concurrency(wf),
    ]
    if publisher_allowlist is not None:
        findings += audit_publisher_trust(wf, publisher_allowlist)
    return findings
