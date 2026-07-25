"""Advisory-watch service (roadmap #7): poll GHSA and join it against the fleet's actions.

Two halves:

* :func:`poll_advisories` — fetch GitHub's *global advisory database* for the ``actions`` ecosystem
  (``GET /advisories``), following ``Link: rel="next"`` up to a page cap. The only network I/O; the
  daily sweep persists the result so the read endpoint stays a couple of indexed reads.
* :func:`scan_fleet` — match the stored advisories against the action refs each
  ``workflow_relations`` row records (the same fleet facts the Deprecation Radar reads), rolling
  hits up into a severity-sorted inventory: per advisory, how many workflows/repos use the action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.audit.advisory_feed import AdvisoryRecord, match_advisories, parse_advisories
from actionsplane.db.models import Advisory, Repo, WorkflowRelation

# RFC 5988 Link header: <https://api.github.com/advisories?page=2>; rel="next"
_NEXT_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "moderate": 2, "low": 3, "unknown": 4}


def _next_link(header: str | None) -> str | None:
    if not header:
        return None
    m = _NEXT_LINK_RE.search(header)
    return m.group(1) if m else None


async def poll_advisories(
    http: httpx.AsyncClient,
    *,
    base_url: str,
    token: str | None = None,
    max_pages: int = 3,
) -> list[AdvisoryRecord]:
    """Fetch actions-ecosystem advisories from the global advisory DB. Pure-ish: all I/O is on the
    passed ``http`` client, so tests drive it with a MockTransport. Raises on a non-2xx response."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params: dict | None = {
        "ecosystem": "actions",
        "per_page": 100,
        "sort": "published",
        "direction": "desc",
    }
    next_url: str | None = f"{base_url.rstrip('/')}/advisories"
    raw: list[dict] = []
    pages = 0
    while next_url and pages < max_pages:
        resp = await http.get(next_url, params=params, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, list):
            raw.extend(body)
        pages += 1
        next_url = _next_link(resp.headers.get("Link"))
        params = None  # subsequent page URLs already carry the query
    return parse_advisories(raw)


def advisory_rows(records: list[AdvisoryRecord], *, fetched_at: datetime) -> list[dict]:
    """Map parsed records to ``advisories``-table column dicts for :func:`upsert_advisories`."""
    return [
        {
            "ghsa_id": r.ghsa_id,
            "package": r.package,
            "severity": r.severity,
            "summary": r.summary,
            "url": r.url,
            "vulnerable_range": r.vulnerable_range,
            "first_patched": r.first_patched,
            "published_at": r.published_at,
            "fetched_at": fetched_at,
        }
        for r in records
    ]


def _to_record(adv: Advisory) -> AdvisoryRecord:
    return AdvisoryRecord(
        ghsa_id=adv.ghsa_id,
        package=adv.package,
        severity=adv.severity,
        summary=adv.summary,
        url=adv.url,
        vulnerable_range=adv.vulnerable_range,
        first_patched=adv.first_patched,
        published_at=adv.published_at,
    )


@dataclass(frozen=True, slots=True)
class AdvisoryImpact:
    ghsa_id: str
    package: str
    severity: str
    summary: str
    url: str
    vulnerable_range: str | None
    first_patched: str | None
    published_at: str | None  # ISO
    workflows: int
    repos: int
    affected_repo_ids: list[int] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AdvisoryReport:
    as_of: str
    advisories_known: int
    workflows_scanned: int
    entries: list[AdvisoryImpact]


def _sort_key(e: AdvisoryImpact) -> tuple[int, int]:
    """Most severe first, then most repos affected."""
    return (_SEVERITY_ORDER.get(e.severity, 4), -e.repos)


async def scan_fleet(
    session: AsyncSession, *, as_of: datetime, sample_limit: int = 20
) -> AdvisoryReport:
    """Join the stored advisories against the fleet's action usage. No GitHub I/O."""
    advisories = [_to_record(a) for a in (await session.scalars(select(Advisory))).all()]
    if not advisories:
        return AdvisoryReport(
            as_of=as_of.isoformat(), advisories_known=0, workflows_scanned=0, entries=[]
        )
    repo_full = {r.id: f"{r.owner}/{r.name}" for r in (await session.scalars(select(Repo))).all()}

    # key = ghsa_id|package -> aggregation state
    workflows: dict[str, int] = {}
    repos: dict[str, set[int]] = {}
    samples: dict[str, list[dict]] = {}
    by_key: dict[str, AdvisoryRecord] = {}

    scanned = 0
    for rel in await session.scalars(select(WorkflowRelation)):
        scanned += 1
        desc = rel.descriptor or {}
        for hit in match_advisories(desc.get("uses", []), advisories):
            key = f"{hit.advisory.ghsa_id}|{hit.advisory.package}"
            by_key.setdefault(key, hit.advisory)
            workflows[key] = workflows.get(key, 0) + 1
            repos.setdefault(key, set()).add(rel.repo_id)
            bucket = samples.setdefault(key, [])
            if len(bucket) < sample_limit:
                bucket.append(
                    {
                        "repo": repo_full.get(rel.repo_id, str(rel.repo_id)),
                        "path": rel.path,
                        "matched": hit.matched,
                    }
                )

    entries = [
        AdvisoryImpact(
            ghsa_id=adv.ghsa_id,
            package=adv.package,
            severity=adv.severity,
            summary=adv.summary,
            url=adv.url,
            vulnerable_range=adv.vulnerable_range,
            first_patched=adv.first_patched,
            published_at=adv.published_at.isoformat() if adv.published_at else None,
            workflows=workflows[key],
            repos=len(repos[key]),
            affected_repo_ids=sorted(repos[key]),
            samples=samples.get(key, []),
        )
        for key, adv in by_key.items()
    ]
    entries.sort(key=_sort_key)
    return AdvisoryReport(
        as_of=as_of.isoformat(),
        advisories_known=len(advisories),
        workflows_scanned=scanned,
        entries=entries,
    )
