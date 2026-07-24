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


def pin_workflow_to_sha(text: str, resolver: ShaResolver) -> EditResult:
    """Pin all mutable action refs in a workflow to commit SHAs (round-trip preserving)."""
    yaml = _rt_yaml()
    data = yaml.load(io.StringIO(text))
    changes: list[str] = []
    if data and "jobs" in data:
        for job in data["jobs"].values():
            if hasattr(job, "get") and "steps" in job:
                _pin_steps(job["steps"], resolver, changes)
    out = io.StringIO()
    yaml.dump(data, out)
    return EditResult(new_text=out.getvalue(), changes=changes)


# Operation registry — name -> callable(text, resolver) -> EditResult
OPERATIONS: dict[str, Callable[..., EditResult]] = {
    "pin-shas": pin_workflow_to_sha,
}
