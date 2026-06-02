"""GitLab include/component pin audit — the supply-chain analog of the Actions pin audit.

GitLab itself now recommends SHA-pinning CI/CD components (same lesson as the `tj-actions`
incident). We classify each `include:` by how it is pinned and emit findings for the unsafe ones,
reusing the shared :class:`PinState` / :class:`Finding` / :class:`FindingType` vocabulary so the
UI and API treat GitLab and GitHub findings uniformly.
"""

from __future__ import annotations

import re

from actionsplane.audit.findings import Finding
from actionsplane.models.enums import FindingType, PinState, Severity
from actionsplane.providers.gitlab.parser import GitLabInclude, GitLabPipeline

_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^v?\d+(\.\d+){0,2}$")
_BRANCHES = {"main", "master", "develop", "dev", "trunk"}


def classify_include(inc: GitLabInclude) -> PinState:
    """Pin state of a single include: SHA/version/branch/unpinned, or LOCAL for in-repo files."""
    if inc.kind in ("local", "template"):
        return PinState.LOCAL  # in-repo or GitLab-managed template — no external pin needed
    if inc.kind == "remote":
        return PinState.UNPINNED  # arbitrary URL, typically unversioned
    ref = inc.ref
    if not ref:
        return PinState.UNPINNED  # project include with no ref tracks the default branch
    if _SHA_RE.match(ref):
        return PinState.SHA_PINNED
    if _VERSION_RE.match(ref):
        return PinState.TAG_PINNED
    if ref in _BRANCHES:
        return PinState.BRANCH_PINNED
    return PinState.TAG_PINNED  # a named tag we can't prove is a branch


def audit_pipeline(pipeline: GitLabPipeline) -> list[Finding]:
    """Flag includes/components that aren't SHA-pinned (mutable supply-chain refs)."""
    findings: list[Finding] = []
    for inc in pipeline.includes:
        label = f"{inc.kind}:{inc.target}" + (f"@{inc.ref}" if inc.ref else "")
        if inc.kind == "unknown":
            findings.append(
                Finding(
                    FindingType.UNVERIFIED_PUBLISHER,
                    Severity.LOW,
                    f"GitLab include `{inc.raw}` has an unrecognised form; review it manually.",
                    ref=label,
                )
            )
            continue
        state = classify_include(inc)
        if state in (PinState.UNPINNED, PinState.BRANCH_PINNED):
            findings.append(
                Finding(
                    FindingType.UNPINNED_ACTION,
                    Severity.HIGH,
                    f"GitLab include `{label}` is {state.value}-pinned; pin to a commit SHA.",
                    ref=label,
                )
            )
        elif state is PinState.TAG_PINNED:
            findings.append(
                Finding(
                    FindingType.UNPINNED_ACTION,
                    Severity.MEDIUM,
                    f"GitLab include `{label}` is version/tag-pinned; tags are mutable — "
                    "pin to a commit SHA.",
                    ref=label,
                )
            )
    return findings
