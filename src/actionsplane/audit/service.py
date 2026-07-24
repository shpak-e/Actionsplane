"""Audit service — run the audit engine over a real repo's workflows (plan §5.2, Phase 2).

Ties the pure pieces together with I/O: fetch each workflow file via the GitHub client, parse it,
run the audit engine, and persist findings with lifecycle tracking (upsert by fingerprint, then
resolve anything no longer present). Returns the number of open findings written.
"""

from __future__ import annotations

import logging

from actionsplane.audit.engine import audit_workflow
from actionsplane.audit.immutable import resolve_immutable_refs
from actionsplane.audit.parser import parse_workflow
from actionsplane.db.models import Repo
from actionsplane.db.repository import (
    resolve_stale_findings,
    upsert_finding,
    upsert_workflow_relation,
)
from actionsplane.github.client import GitHubClient
from actionsplane.observability import span
from actionsplane.relations import extract_relations

log = logging.getLogger(__name__)


async def audit_repo(
    session,
    gh: GitHubClient,
    repo: Repo,
    *,
    publisher_allowlist: set[str] | None = None,
) -> int:
    """Audit every workflow in a repo, persist findings, resolve stale ones. Returns count."""
    with span("audit.audit_repo", **{"repo": f"{repo.owner}/{repo.name}", "repo_id": repo.id}):
        return await _audit_repo(session, gh, repo, publisher_allowlist=publisher_allowlist)


async def _audit_repo(
    session,
    gh: GitHubClient,
    repo: Repo,
    *,
    publisher_allowlist: set[str] | None = None,
) -> int:
    # Phase 1 — all GitHub I/O + pure analysis, BEFORE touching the DB (review §5 M2). An async
    # session only checks a connection out of the pool on its first query, so doing every fetch
    # here means the connection stays in the pool during the slow network calls instead of being
    # held across them — which is what let concurrent sweeps exhaust the pool.
    parsed: list[tuple[str, object]] = []  # (path, workflow)
    for path in await gh.list_workflow_files(repo.owner, repo.name):
        try:
            text = await gh.get_file_text(repo.owner, repo.name, path)
            parsed.append((path, parse_workflow(text, path)))
        except Exception:  # one bad file should not abort the whole repo audit
            log.warning("skipping unparseable workflow %s/%s:%s", repo.owner, repo.name, path)

    # Still Phase 1 I/O: resolve which tag-pinned refs across all this repo's workflows are backed
    # by immutable releases, so the audit doesn't nag to SHA-pin a tag that's already tamper-proof
    # (W1). One bounded, ETag-cacheable lookup per distinct tag ref; done before the DB writes.
    all_uses = {ref for _, wf in parsed for ref in wf.all_uses()}
    immutable_refs = await resolve_immutable_refs(gh, all_uses)

    # Phase 2 — DB writes only (connection checked out here, released on commit).
    seen: set[str] = set()
    written = 0
    for path, wf in parsed:
        await upsert_workflow_relation(
            session, repo_id=repo.id, path=path, name=wf.name, descriptor=extract_relations(wf)
        )
        for finding in audit_workflow(
            wf, publisher_allowlist=publisher_allowlist, immutable_refs=immutable_refs
        ):
            row = finding.as_row(repo_id=repo.id, path=path)
            seen.add(row["fingerprint"])
            await upsert_finding(session, row)
            written += 1
    await resolve_stale_findings(session, repo_id=repo.id, seen_fingerprints=seen)
    await session.commit()
    return written
