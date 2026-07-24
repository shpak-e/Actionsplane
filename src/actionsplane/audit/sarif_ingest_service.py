"""Persist ingested SARIF findings with per-source lifecycle (D1).

Wraps the pure parser (:mod:`sarif_ingest`) with the DB write: upsert each result as a finding and
resolve the ones a scanner no longer reports — scoped to that scanner's ``tool:`` prefix, so an
external ingest never disturbs ActionsPlane's own findings or another tool's. Same fingerprint
lifecycle as the native audit, so ingested findings dedup and auto-resolve identically.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.audit.sarif_ingest import parse_sarif, tools_in
from actionsplane.db.repository import resolve_stale_findings_for_source, upsert_finding

log = logging.getLogger(__name__)


async def ingest_sarif(session: AsyncSession, *, repo_id: int, doc: dict) -> dict:
    """Ingest one SARIF document for a repo. Returns ``{ingested, resolved, tools}``."""
    findings = parse_sarif(doc)
    seen_by_tool: dict[str, set[str]] = {}
    for f in findings:
        row = f.as_row(repo_id=repo_id)
        await upsert_finding(session, row)
        tool = f.finding_type.split(":", 1)[0]
        seen_by_tool.setdefault(tool, set()).add(row["fingerprint"])

    # Resolve stale findings per tool that appeared in this document. A tool present in the doc but
    # with zero results still gets a resolve pass (empty seen set) so its last finding can close.
    resolved = 0
    for tool in tools_in(doc):
        resolved += await resolve_stale_findings_for_source(
            session,
            repo_id=repo_id,
            type_prefix=f"{tool}:",
            seen_fingerprints=seen_by_tool.get(tool, set()),
        )
    await session.commit()
    log.info(
        "ingested SARIF for repo %s: %d findings, %d resolved", repo_id, len(findings), resolved
    )
    return {"ingested": len(findings), "resolved": resolved, "tools": sorted(tools_in(doc))}
