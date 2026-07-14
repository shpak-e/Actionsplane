"""Campaign execution service (plan §5.4, Phase 4).

Turns a bulk-edit operation into per-repo dry-run diffs and, on explicit apply, into PRs.
Invariants from the security model: **all edits go through PRs** (never direct-to-main), apply
is gated and human-approved, and the SHA resolver is GitHub-backed but pre-resolved so the
rewrite stays a pure function.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from actionsplane.audit.parser import parse_workflow
from actionsplane.audit.pins import classify
from actionsplane.executor.operations import OPERATIONS, unified_diff
from actionsplane.github.client import GitHubClient
from actionsplane.models.enums import PinState

log = logging.getLogger(__name__)

# Operations whose full dry-run path (ref resolution + rewrite) is implemented here. The registry
# in operations.py may list more rewrite callables than have an end-to-end dry-run, so dispatch is
# guarded rather than assumed (review §4 L-1: a campaign must never silently run pin-shas under
# another operation's label).
_DRY_RUN_SUPPORTED = {"pin-shas"}


@dataclass(frozen=True, slots=True)
class FileEdit:
    path: str
    blob_sha: str  # existing blob sha, required to update the file via the contents API
    new_text: str
    diff: str
    changes: list[str]


async def _resolve_pin_refs(
    gh: GitHubClient, text: str, path: str
) -> dict[tuple[str, str, str], str]:
    """Resolve every mutable action ref in a workflow to a commit SHA (against the action repo).

    Refs come from the parsed AST's ``all_uses()`` (review §4 L-2) — ``jobs.*.uses`` and
    ``jobs.*.steps[].uses`` only — not a line scan, so a ``uses:`` substring buried in a ``run:``
    heredoc in an untrusted repo can't steer a ``get_commit_sha`` at an attacker-chosen action.
    If the file doesn't parse we skip resolution (the caller's pin pass is then a no-op for it).
    """
    resolved: dict[tuple[str, str, str], str] = {}
    try:
        wf = parse_workflow(text, path)
    except Exception:
        log.warning("skipping pin resolution for unparseable workflow %s", path)
        return resolved
    for ref in set(wf.all_uses()):
        u = classify(ref)
        if u.pin_state in (PinState.TAG_PINNED, PinState.BRANCH_PINNED) and u.owner and u.repo:
            resolved[(u.owner, u.repo, u.ref)] = await gh.get_commit_sha(u.owner, u.repo, u.ref)
    return resolved


async def dry_run_repo(
    gh: GitHubClient,
    owner: str,
    repo: str,
    *,
    operation: str = "pin-shas",
    resolved: dict[tuple[str, str, str], str] | None = None,
) -> tuple[list[FileEdit], dict[tuple[str, str, str], str]]:
    """Compute edits for ``operation`` across every workflow in a repo. No writes.

    Dispatches on ``operation`` so a campaign labeled one thing can never silently run another
    (review §4 L-1). Only ``pin-shas`` has a full dry-run path today; an unimplemented (but
    registry-known) operation raises rather than falling through to pinning. On dry-run
    (``resolved=None``) tags are resolved against the action repos' HEAD and the resolved map is
    returned. On **apply**, pass the previously-resolved map back in so the SHAs that land are
    exactly the ones the reviewer saw — a tag retargeted between preview and apply cannot change it.
    """
    if operation not in _DRY_RUN_SUPPORTED:
        raise NotImplementedError(f"dry-run for operation {operation!r} is not implemented")
    edit_fn = OPERATIONS[operation]
    accumulated: dict[tuple[str, str, str], str] = dict(resolved or {})
    edits: list[FileEdit] = []
    for path in await gh.list_workflow_files(owner, repo):
        f = await gh.get_file(owner, repo, path)
        if resolved is None:
            accumulated.update(await _resolve_pin_refs(gh, f["text"], path))
        result = edit_fn(f["text"], lambda o, r, ref, _m=accumulated: _m.get((o, r, ref)))
        if result.changed:
            edits.append(
                FileEdit(
                    path=path,
                    blob_sha=f["sha"],
                    new_text=result.new_text,
                    diff=unified_diff(path, f["text"], result.new_text),
                    changes=result.changes,
                )
            )
    return edits, accumulated


def _md_inline(s: str) -> str:
    """Neutralize repo-controlled text before it lands in an ActionsPlane-authored PR body (§4 L-3).

    Change strings and paths derive from an untrusted repo's ``uses:`` refs, so a crafted action
    name could break out of the code span (backticks) or inject list structure (newlines). Collapse
    both rather than render attacker markdown in our own PR.
    """
    return s.replace("`", "'").replace("\r", " ").replace("\n", " ")


async def open_pr_for_edits(
    gh: GitHubClient,
    owner: str,
    repo: str,
    edits: list[FileEdit],
    *,
    base_branch: str,
    operation_id: str,
    rationale: str,
) -> dict:
    """Branch, commit each edit, and open a PR. Returns {"number", "html_url"}."""
    branch = f"actionsplane/{operation_id}"
    base_sha = await gh.get_ref_sha(owner, repo, base_branch)
    try:
        await gh.create_branch(owner, repo, branch, base_sha)
    except httpx.HTTPStatusError as exc:
        # 422 = ref already exists (a prior attempt); reuse it rather than failing the retry
        if exc.response.status_code != 422:
            raise
    for edit in edits:
        await gh.put_file(
            owner,
            repo,
            edit.path,
            text=edit.new_text,
            message=f"ci: {operation_id} — {edit.path}",
            branch=branch,
            sha=edit.blob_sha,
        )
    lines = [
        f"- `{_md_inline(e.path)}`: " + "; ".join(_md_inline(c) for c in e.changes) for e in edits
    ]
    body = rationale + "\n\n" + "\n".join(lines)
    return await gh.create_pull_request(
        owner, repo, head=branch, base=base_branch, title=f"ci: {operation_id}", body=body
    )
