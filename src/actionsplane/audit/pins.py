"""Pin classifier — the heart of the supply-chain audit (plan §5.2).

Every ``uses:`` reference is classified into a :class:`PinState`. SHA-pinning is the only
form that defends against the tag-retargeting class of attack (e.g. the
``tj-actions/changed-files`` incident, where ``@v1`` was silently repointed at malicious
code). This module is intentionally pure — no I/O — so it is trivially testable and can run
over a whole org's worth of references in-process.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from actionsplane.models.enums import PinState

# A git commit SHA: 40 hex chars (SHA-1) or 64 (SHA-256, future-proofing).
_SHA_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
# A version tag like v4, v4.1, v4.1.2, 4.1.2 (with optional leading v).
_VERSION_TAG_RE = re.compile(r"^v?\d+(\.\d+){0,2}$")
# Common default branch names — treated as branch pins (mutable, unsafe).
_BRANCH_NAMES = {"main", "master", "develop", "dev", "trunk"}


@dataclass(frozen=True, slots=True)
class UsesRef:
    """A parsed ``uses:`` reference."""

    raw: str
    owner: str | None
    repo: str | None
    subpath: str | None
    ref: str | None  # the bit after '@'
    pin_state: PinState

    @property
    def action(self) -> str | None:
        """``owner/repo`` for a marketplace action, else None (local/docker)."""
        if self.owner and self.repo:
            return f"{self.owner}/{self.repo}"
        return None


def ref_key(owner: str, repo: str, ref: str) -> str:
    """Canonical ``owner/repo@ref`` key used to look a reference up in an immutable-ref set."""
    return f"{owner}/{repo}@{ref}"


def classify(uses: str, *, immutable_refs: frozenset[str] | set[str] | None = None) -> UsesRef:
    """Classify a single ``uses:`` reference string.

    ``immutable_refs`` is an optional set of ``owner/repo@ref`` strings **proven** (via the API) to
    be backed by a GitHub immutable release; a tag-pinned ref found there is upgraded from
    ``TAG_PINNED`` to ``IMMUTABLE``. Default ``None`` means "nothing proven immutable" — so
    behaviour is unchanged and a tag is never optimistically treated as safe.

    Examples
    --------
    >>> classify("actions/checkout@8f4b7f8...").pin_state  # 40-hex sha
    <PinState.SHA_PINNED: 'sha'>
    >>> classify("actions/checkout@v4").pin_state
    <PinState.TAG_PINNED: 'tag'>
    >>> classify("actions/checkout@main").pin_state
    <PinState.BRANCH_PINNED: 'branch'>
    >>> classify("./.github/actions/setup").pin_state
    <PinState.LOCAL: 'local'>
    """
    raw = uses.strip()

    # Local action: relative path, no pinning concept.
    if raw.startswith("./") or raw.startswith("../"):
        return UsesRef(raw, None, None, raw, None, PinState.LOCAL)

    # Docker action.
    if raw.startswith("docker://"):
        return UsesRef(raw, None, None, raw, None, PinState.DOCKER)

    name, sep, ref = raw.partition("@")
    owner, repo, subpath = _split_action_name(name)

    if not sep or not ref:
        # No ref at all — e.g. "actions/checkout". Unpinned (resolves to default branch).
        return UsesRef(raw, owner, repo, subpath, None, PinState.UNPINNED)

    state = _classify_ref(ref)
    if (
        state is PinState.TAG_PINNED
        and immutable_refs
        and owner
        and repo
        and ref_key(owner, repo, ref) in immutable_refs
    ):
        state = PinState.IMMUTABLE
    return UsesRef(raw, owner, repo, subpath, ref, state)


def _classify_ref(ref: str) -> PinState:
    if _SHA_RE.match(ref):
        return PinState.SHA_PINNED
    if _VERSION_TAG_RE.match(ref):
        return PinState.TAG_PINNED
    if ref in _BRANCH_NAMES:
        return PinState.BRANCH_PINNED
    # An arbitrary ref (e.g. "@stable", "@release-2024") we can't prove is immutable. Treat
    # as UNKNOWN — it might be a branch dressed as a tag, and the audit should escalate.
    return PinState.UNKNOWN_REF


def _split_action_name(name: str) -> tuple[str | None, str | None, str | None]:
    """Split ``owner/repo/sub/path`` -> (owner, repo, subpath-or-None)."""
    parts = name.split("/")
    if len(parts) < 2:
        return None, None, name or None
    owner, repo = parts[0], parts[1]
    subpath = "/".join(parts[2:]) or None
    return owner, repo, subpath


def is_pinned_safely(uses: str, *, immutable_refs: frozenset[str] | set[str] | None = None) -> bool:
    """True for the supply-chain-safe pin states: SHA-pinned, local, or a proven immutable tag."""
    return classify(uses, immutable_refs=immutable_refs).pin_state in (
        PinState.SHA_PINNED,
        PinState.IMMUTABLE,
        PinState.LOCAL,
    )
