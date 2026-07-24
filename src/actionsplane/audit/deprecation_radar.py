"""Deprecation Radar service (W8): join the feed against the fleet's stored workflow facts.

Reads the runner labels + action refs the audit persisted on each ``workflow_relations`` row and
matches them against :mod:`deprecation_feed`, rolling the hits up into a deadline-sorted impact
inventory: per deprecation, how many workflows/repos are affected, how long until the deadline, and
the repo ids a migration campaign would target. No GitHub I/O — a couple of indexed reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane.audit.deprecation_feed import FEED, DeprecationEntry, match_facts
from actionsplane.db.models import Repo, WorkflowRelation

_URGENT_DAYS = 90


@dataclass(frozen=True, slots=True)
class RadarEntry:
    id: str
    kind: str
    target: str
    replacement: str
    deadline: str | None  # ISO date
    days_until: int | None
    status: str  # overdue | urgent | upcoming | no-date
    reference: str
    fix_operation: str | None
    workflows: int
    repos: int
    fixable_repo_ids: list[int] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RadarReport:
    as_of: str
    workflows_scanned: int
    entries: list[RadarEntry]


def _status(days_until: int | None) -> str:
    if days_until is None:
        return "no-date"
    if days_until < 0:
        return "overdue"
    if days_until <= _URGENT_DAYS:
        return "urgent"
    return "upcoming"


def _sort_key(e: RadarEntry) -> tuple[int, int]:
    """Overdue first, then soonest deadline; dateless entries last."""
    order = {"overdue": 0, "urgent": 1, "upcoming": 2, "no-date": 3}
    return (order[e.status], e.days_until if e.days_until is not None else 10**9)


async def scan_fleet(
    session: AsyncSession,
    *,
    as_of: date,
    feed: tuple[DeprecationEntry, ...] = FEED,
    sample_limit: int = 20,
) -> RadarReport:
    repo_full = {r.id: f"{r.owner}/{r.name}" for r in (await session.scalars(select(Repo))).all()}

    # entry id -> aggregation state
    workflows: dict[str, int] = {}
    repos: dict[str, set[int]] = {}
    samples: dict[str, list[dict]] = {}

    scanned = 0
    for rel in await session.scalars(select(WorkflowRelation)):
        scanned += 1
        desc = rel.descriptor or {}
        for hit in match_facts(desc.get("runs_on", []), desc.get("uses", []), feed):
            eid = hit.entry.id
            workflows[eid] = workflows.get(eid, 0) + 1
            repos.setdefault(eid, set()).add(rel.repo_id)
            bucket = samples.setdefault(eid, [])
            if len(bucket) < sample_limit:
                bucket.append(
                    {
                        "repo": repo_full.get(rel.repo_id, str(rel.repo_id)),
                        "path": rel.path,
                        "matched": hit.matched,
                    }
                )

    entries: list[RadarEntry] = []
    for entry in feed:
        if entry.id not in workflows:
            continue  # nothing in the fleet hits it — omit from the inventory
        days = (entry.deadline - as_of).days if entry.deadline else None
        entries.append(
            RadarEntry(
                id=entry.id,
                kind=entry.kind,
                target=entry.target,
                replacement=entry.replacement,
                deadline=entry.deadline.isoformat() if entry.deadline else None,
                days_until=days,
                status=_status(days),
                reference=entry.reference,
                fix_operation=entry.fix_operation,
                workflows=workflows[entry.id],
                repos=len(repos[entry.id]),
                fixable_repo_ids=sorted(repos[entry.id]),
                samples=samples.get(entry.id, []),
            )
        )

    entries.sort(key=_sort_key)
    return RadarReport(as_of=as_of.isoformat(), workflows_scanned=scanned, entries=entries)
