"""Campaign execution service (plan §5.4, Phase 4).

Turns a bulk-edit operation into per-repo dry-run diffs and, on explicit apply, into PRs.
Invariants from the security model: **all edits go through PRs** (never direct-to-main), apply
is gated and human-approved, and the SHA resolver is GitHub-backed but pre-resolved so the
rewrite stays a pure function.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from actionsplane.audit.pins import classify
from actionsplane.executor.operations import pin_workflow_to_sha, unified_diff
from actionsplane.github.client import GitHubClient
from actionsplane.models.enums import PinState


@dataclass(frozen=True, slots=True)
class FileEdit:
    path: str
    blob_sha: str  # existing blob sha, required to update the file via the contents API
    new_text: str
    diff: str
    changes: list[str]


async def _resolve_pin_refs(gh: GitHubClient, text: str) -> dict[tuple[str, str, str], str]:
    """Resolve every mutable action ref in a workflow to a commit SHA (against the action repo)."""
    resolved: dict[tuple[str, str, str], str] = {}
    seen: set[str] = set()
    # cheap scan: classify each `uses:` line without a full parse
    for line in text.splitlines():
        stripped = line.strip().lstrip("- ")
        if not stripped.startswith("uses:"):
            continue
        ref = stripped.split("uses:", 1)[1].strip().strip("'\"")
        if ref in seen:
            continue
        seen.add(ref)
        u = classify(ref)
        if u.pin_state in (PinState.TAG_PINNED, PinState.BRANCH_PINNED) and u.owner and u.repo:
            sha = await gh.get_commit_sha(u.owner, u.repo, u.ref)
            resolved[(u.owner, u.repo, u.ref)] = sha
    return resolved


async def dry_run_repo(
    gh: GitHubClient,
    owner: str,
    repo: str,
    *,
    resolved: dict[tuple[str, str, str], str] | None = None,
) -> tuple[list[FileEdit], dict[tuple[str, str, str], str]]:
    """Compute pin-to-SHA edits for every workflow in a repo. No writes.

    On dry-run (``resolved=None``) tags are resolved against the action repos' HEAD and the
    resolved map is returned. On **apply**, pass the previously-resolved map back in so the SHAs
    that land are exactly the ones the reviewer saw — a tag retargeted between preview and apply
    cannot change the result.
    """
    accumulated: dict[tuple[str, str, str], str] = dict(resolved or {})
    edits: list[FileEdit] = []
    for path in await gh.list_workflow_files(owner, repo):
        f = await gh.get_file(owner, repo, path)
        if resolved is None:
            accumulated.update(await _resolve_pin_refs(gh, f["text"]))
        result = pin_workflow_to_sha(
            f["text"], lambda o, r, ref, _m=accumulated: _m.get((o, r, ref))
        )
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
    lines = [f"- `{e.path}`: " + "; ".join(e.changes) for e in edits]
    body = rationale + "\n\n" + "\n".join(lines)
    return await gh.create_pull_request(
        owner, repo, head=branch, base=base_branch, title=f"ci: {operation_id}", body=body
    )
