"""Curated, versioned deprecation feed + pure matcher (W8 — Deprecation Radar).

GitHub retires runner images and action majors on a schedule; every wave is the same fleet
scavenger hunt. This is the machine-readable feed of those deprecations — runner labels and action
versions with hard deadlines and a replacement — plus a pure matcher that, given a workflow's
``runs-on`` labels and ``uses`` refs (persisted in the relation descriptor, so no re-fetch), reports
which entries it hits. The radar service joins this across the fleet into a deadline-sorted
inventory.

Dates are the published GitHub schedule as of 2026-07 (see each entry's reference). Keep this list
reviewed — it is the product's freshness. Known blind spot: a SHA-pinned action hides its version,
so an action-version entry can't see ``upload-artifact@v3`` behind a bare SHA (the roadmap's
SHA→dead-version resolution is a follow-up); runner-label entries are unaffected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from actionsplane.audit.pins import classify

_MAJOR_RE = re.compile(r"^(v?\d+)")


@dataclass(frozen=True, slots=True)
class DeprecationEntry:
    id: str
    kind: str  # "runner-label" | "action-version"
    target: str  # runner label (e.g. "ubuntu-22.04") or "owner/repo@major" (e.g. "actions/x@v3")
    replacement: str
    deadline: date | None  # hard removal / brownout date; None = announced, no fixed date
    reference: str
    # The campaign operation that would remediate this fleet-wide. Some ops don't exist yet
    # (tracked as W8 follow-up); surfaced so the UI can offer the one-click campaign when they land.
    fix_operation: str | None = None


# --- the feed ---------------------------------------------------------------------------------
FEED: tuple[DeprecationEntry, ...] = (
    DeprecationEntry(
        "ubuntu-20.04-retired",
        "runner-label",
        "ubuntu-20.04",
        "ubuntu-24.04",
        date(2025, 4, 15),
        "https://github.blog/changelog/2025-ubuntu-20-04-retirement",
        "swap-runs-on",
    ),
    DeprecationEntry(
        "ubuntu-22.04-retirement",
        "runner-label",
        "ubuntu-22.04",
        "ubuntu-24.04",
        date(2026, 9, 17),  # brownouts start; scheduled jobs FAIL from here
        "https://github.blog/changelog/2026-ubuntu-22-04-retirement",
        "swap-runs-on",
    ),
    DeprecationEntry(
        "macos-14-retirement",
        "runner-label",
        "macos-14",
        "macos-15",
        date(2026, 11, 2),
        "https://github.blog/changelog/2026-macos-14-retirement",
        "swap-runs-on",
    ),
    DeprecationEntry(
        "upload-artifact-v3-shutdown",
        "action-version",
        "actions/upload-artifact@v3",
        "actions/upload-artifact@v4",
        date(2025, 1, 30),
        "https://github.blog/changelog/2024-artifact-v3-deprecation",
        "bump-action-version",
    ),
    DeprecationEntry(
        "download-artifact-v3-shutdown",
        "action-version",
        "actions/download-artifact@v3",
        "actions/download-artifact@v4",
        date(2025, 1, 30),
        "https://github.blog/changelog/2024-artifact-v3-deprecation",
        "bump-action-version",
    ),
    DeprecationEntry(
        "cache-v2-deprecated",
        "action-version",
        "actions/cache@v2",
        "actions/cache@v4",
        date(2025, 3, 1),
        "https://github.com/actions/cache/discussions",
        "bump-action-version",
    ),
)


@dataclass(frozen=True, slots=True)
class DeprecationHit:
    entry: DeprecationEntry
    matched: str  # the exact label / ref in the workflow that matched


def _major(ref: str | None) -> str | None:
    if not ref:
        return None
    m = _MAJOR_RE.match(ref)
    return m.group(1) if m else None


def match_facts(
    runs_on: list[str], uses: list[str], feed: tuple[DeprecationEntry, ...] = FEED
) -> list[DeprecationHit]:
    """Pure: which feed entries does this workflow (its runner labels + action refs) hit?"""
    labels = set(runs_on)
    hits: list[DeprecationHit] = []
    for entry in feed:
        if entry.kind == "runner-label":
            if entry.target in labels:
                hits.append(DeprecationHit(entry, entry.target))
        elif entry.kind == "action-version":
            want_action, _, want_major = entry.target.partition("@")
            for ref in uses:
                u = classify(ref)
                if u.action == want_action and _major(u.ref) == want_major:
                    hits.append(DeprecationHit(entry, ref))
                    break
    return hits
