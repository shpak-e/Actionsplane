"""Bulk-edit operations (plan §5.4, Phase 4).

Each operation rewrites a workflow file and returns the new text + a list of human-readable
changes. Rewrites use ``ruamel.yaml`` round-trip parsing so comments, key order, and formatting
survive — the resulting PR diff is minimal and reviewable, which is the whole trust thesis.

``pin_workflow_to_sha`` is the flagship operation (the #1 demo): resolve every mutable
``uses: owner/repo@tag`` to a full commit SHA and leave the original tag as a trailing comment,
matching the community convention (``uses: actions/checkout@<sha>  # v4``). The tag→SHA lookup is
injected as a callable so the rewrite itself is pure and unit-testable without network access.
"""

from __future__ import annotations

import difflib
import io
from collections.abc import Callable
from dataclasses import dataclass

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from actionsplane.audit.pins import classify
from actionsplane.models.enums import PinState

# resolver(owner, repo, ref) -> 40-char commit SHA (or None if it can't be resolved)
ShaResolver = Callable[[str, str, str], str | None]


def unified_diff(path: str, before: str, after: str) -> str:
    """A git-style unified diff between two versions of a file (empty string if identical)."""
    if before == after:
        return ""
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(lines)


@dataclass(frozen=True, slots=True)
class EditResult:
    new_text: str
    changes: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def _rt_yaml() -> YAML:
    yaml = YAML()  # round-trip mode (default) preserves comments + formatting
    yaml.preserve_quotes = True
    yaml.width = 4096  # don't wrap long lines
    yaml.indent(mapping=2, sequence=4, offset=2)  # GitHub Actions house style
    return yaml


def _eol_comment(node, key: str) -> str | None:
    """Return the existing end-of-line comment text for a mapping key, if any."""
    token = node.ca.items.get(key) if hasattr(node, "ca") else None
    if token and token[2] is not None:
        return token[2].value.lstrip("#").strip().rstrip("\n")
    return None


def _pin_steps(steps, resolver: ShaResolver, changes: list[str]) -> None:
    """Rewrite each step's `uses:` in place (mutates the ruamel sequence).

    Immutability is handled upstream: the campaign's resolver returns None for a tag backed by an
    immutable release (it's deliberately excluded from the resolved SHA map), so such a tag falls
    through the ``not sha`` guard below and is left as-is — never rewritten to a raw SHA (W1).
    """
    for step in steps:
        if not hasattr(step, "get") or "uses" not in step:
            continue
        ref = step["uses"]
        u = classify(ref)
        if u.pin_state not in (PinState.TAG_PINNED, PinState.BRANCH_PINNED):
            continue  # already a SHA, or local/docker — leave it
        if not (u.owner and u.repo and u.ref):
            continue
        sha = resolver(u.owner, u.repo, u.ref)
        if not sha:
            continue
        sub = f"/{u.subpath}" if u.subpath else ""
        step["uses"] = f"{u.owner}/{u.repo}{sub}@{sha}"
        # annotate with the human-readable tag, preserving any existing trailing comment
        existing = _eol_comment(step, "uses")
        annotation = f"{existing} {u.ref}".strip() if existing else u.ref
        step.yaml_add_eol_comment(annotation, "uses")
        changes.append(f"{ref} → {u.owner}/{u.repo}{sub}@{sha[:12]}…  # {u.ref}")


def pin_workflow_to_sha(
    text: str, resolver: ShaResolver | None = None, *, params: dict | None = None
) -> EditResult:
    """Pin all mutable action refs in a workflow to commit SHAs (round-trip preserving)."""
    yaml = _rt_yaml()
    data = yaml.load(io.StringIO(text))
    changes: list[str] = []
    if data and "jobs" in data:
        for job in data["jobs"].values():
            if hasattr(job, "get") and "steps" in job:
                _pin_steps(job["steps"], resolver or (lambda *_: None), changes)
    out = io.StringIO()
    yaml.dump(data, out)
    return EditResult(new_text=out.getvalue(), changes=changes)


def _iter_jobs(data):
    """Yield (job_id, job) for each mapping job (skips non-mapping / malformed entries)."""
    for jid, job in (data.get("jobs") or {}).items() if data else []:
        if hasattr(job, "get"):
            yield jid, job


def set_workflow_permissions(
    text: str, resolver: ShaResolver | None = None, *, params: dict | None = None
) -> EditResult:
    """Set a top-level ``permissions:`` block (default least-privilege ``contents: read``).

    Fixes both *missing* permissions (adds the block, inserted right after ``on:`` so the diff reads
    naturally) and *broad* permissions (replaces ``write-all`` / over-scoped grants). ``params`` may
    carry ``{"permissions": {...}}`` for a specific scope map. Idempotent — an already-matching
    block is left untouched (no-op, empty diff).
    """
    desired = (params or {}).get("permissions") or {"contents": "read"}
    yaml = _rt_yaml()
    data = yaml.load(io.StringIO(text))
    if not data:
        return EditResult(new_text=text, changes=[])
    if data.get("permissions") == desired:
        return EditResult(new_text=text, changes=[])

    block = CommentedMap()
    for k, v in desired.items():
        block[k] = v
    if "permissions" in data:
        data["permissions"] = block
    else:
        keys = list(data.keys())
        pos = next((keys.index(a) + 1 for a in ("on", "name") if a in keys), 0)
        data.insert(pos, "permissions", block)

    desc = ", ".join(f"{k}: {v}" for k, v in desired.items())
    out = io.StringIO()
    yaml.dump(data, out)
    return EditResult(new_text=out.getvalue(), changes=[f"set workflow permissions ({desc})"])


def swap_runs_on(
    text: str, resolver: ShaResolver | None = None, *, params: dict | None = None
) -> EditResult:
    """Replace a ``runs-on`` label across all jobs (default ubuntu-22.04 → ubuntu-24.04, the W8
    brownout migration). Handles both the scalar (``runs-on: ubuntu-22.04``) and list
    (``runs-on: [self-hosted, ubuntu-22.04]``) forms; ``params`` overrides via ``{"from","to"}``."""
    src = (params or {}).get("from") or "ubuntu-22.04"
    dst = (params or {}).get("to") or "ubuntu-24.04"
    yaml = _rt_yaml()
    data = yaml.load(io.StringIO(text))
    changes: list[str] = []
    for jid, job in _iter_jobs(data):
        ro = job.get("runs-on")
        if isinstance(ro, str) and ro == src:
            job["runs-on"] = dst
            changes.append(f"job `{jid}`: runs-on {src} → {dst}")
        elif isinstance(ro, list):
            for i, label in enumerate(ro):
                if label == src:
                    ro[i] = dst
                    changes.append(f"job `{jid}`: runs-on label {src} → {dst}")
    out = io.StringIO()
    yaml.dump(data, out)
    return EditResult(new_text=out.getvalue(), changes=changes)


def bump_action_version(
    text: str, resolver: ShaResolver | None = None, *, params: dict | None = None
) -> EditResult:
    """Bump every ``uses:`` of a given action to a target ref (e.g. actions/setup-node → v4).

    ``params`` must carry ``{"action": "owner/repo", "to": "v4"}``; without them it's a no-op. The
    subpath (``owner/repo/dir@ref``) and any trailing comment are preserved. For deprecated-action
    findings and the Deprecation Radar's version migrations."""
    action = ((params or {}).get("action") or "").strip().lower()
    dst = (params or {}).get("to")
    yaml = _rt_yaml()
    data = yaml.load(io.StringIO(text))
    changes: list[str] = []
    if not action or not dst:
        return EditResult(new_text=text, changes=[])
    for _jid, job in _iter_jobs(data):
        for step in job.get("steps") or []:
            if not hasattr(step, "get") or "uses" not in step:
                continue
            ref = step["uses"]
            u = classify(ref)
            if u.owner and u.repo and f"{u.owner}/{u.repo}".lower() == action and u.ref != dst:
                sub = f"/{u.subpath}" if u.subpath else ""
                step["uses"] = f"{u.owner}/{u.repo}{sub}@{dst}"
                changes.append(f"{ref} → {u.owner}/{u.repo}{sub}@{dst}")
    out = io.StringIO()
    yaml.dump(data, out)
    return EditResult(new_text=out.getvalue(), changes=changes)


def add_concurrency(
    text: str, resolver: ShaResolver | None = None, *, params: dict | None = None
) -> EditResult:
    """Add a top-level ``concurrency:`` block so overlapping runs on the same ref can't race
    (fixes MISSING_CONCURRENCY). Default group is ``${{ github.workflow }}-${{ github.ref }}`` with
    ``cancel-in-progress: true`` — the community convention that supersedes an in-flight run when a
    new commit lands on the same branch/PR. ``params`` overrides group/cancel_in_progress.

    Idempotent: a workflow that already has a ``concurrency:`` key is left untouched (empty diff).
    Inserted right after ``permissions:``/``on:``/``name:`` so the diff reads naturally.
    """
    group = (params or {}).get("group") or "${{ github.workflow }}-${{ github.ref }}"
    cancel = (params or {}).get("cancel_in_progress")
    cancel = True if cancel is None else bool(cancel)
    yaml = _rt_yaml()
    data = yaml.load(io.StringIO(text))
    if not data or "concurrency" in data:
        return EditResult(new_text=text, changes=[])

    block = CommentedMap()
    block["group"] = group
    block["cancel-in-progress"] = cancel
    keys = list(data.keys())
    pos = next(
        (keys.index(a) + 1 for a in ("permissions", "on", "name") if a in keys),
        0,
    )
    data.insert(pos, "concurrency", block)

    out = io.StringIO()
    yaml.dump(data, out)
    return EditResult(
        new_text=out.getvalue(),
        changes=[f"add concurrency group `{group}` (cancel-in-progress: {str(cancel).lower()})"],
    )


def sync_to_template(
    text: str, resolver: ShaResolver | None = None, *, params: dict | None = None
) -> EditResult:
    """Replace a drifted workflow with its canonical template (the Drift → Act path).

    ``params["canonical_yaml"]`` carries the template body (from the binding's drift detail); this
    op overwrites the file with it verbatim. Deliberately whole-file, not a structural merge — the
    diff the reviewer sees in the PR is exactly the drift, and applying it makes the file identical
    to the template. Scoped to the single bound file by ``params["path"]`` in the dry-run layer, so
    it never stamps the template over other workflows. No-op if the file already matches."""
    canonical = (params or {}).get("canonical_yaml")
    if not canonical or canonical == text:
        return EditResult(new_text=text, changes=[])
    return EditResult(new_text=canonical, changes=["sync workflow to canonical template"])


# Operation registry — name -> callable(text, resolver=None, *, params=None) -> EditResult
OPERATIONS: dict[str, Callable[..., EditResult]] = {
    "pin-shas": pin_workflow_to_sha,
    "set-permissions": set_workflow_permissions,
    "swap-runs-on": swap_runs_on,
    "bump-action-version": bump_action_version,
    "add-concurrency": add_concurrency,
    "sync-template": sync_to_template,
}

# Which audit finding types an operation is expected to close — the basis of the verified-closed
# loop (D2/P1). When a fix campaign is created, the open findings of these types in its target repos
# are snapshotted; after the PRs merge and the repo is re-audited, the ones now resolved are the
# campaign's verified closures. Ops with no finding mapping (runner swaps, template sync) drive PRs
# but aren't finding-backed, so they simply have no verification.
OPERATION_FINDING_TYPES: dict[str, frozenset[str]] = {
    "pin-shas": frozenset({"unpinned_action"}),
    "set-permissions": frozenset({"missing_permissions", "broad_permissions"}),
    "add-concurrency": frozenset({"missing_concurrency"}),
    "bump-action-version": frozenset({"deprecated_action"}),
}
