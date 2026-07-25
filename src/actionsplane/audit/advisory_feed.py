"""Advisory-watch feed (roadmap #7): parse GHSA advisories and match them against fleet actions.

Pure, I/O-free — the network fetch lives in :mod:`advisory_watch`, so parsing and matching are
trivially testable over fixtures. Two responsibilities:

* :func:`parse_advisories` — flatten a GitHub *global advisory DB* JSON response
  (``GET /advisories``) into one :class:`AdvisoryRecord` per affected ``actions``-ecosystem package.
* :func:`match_advisories` — given the action refs a workflow ``uses:``, return the advisories that
  name one of those actions. Matching is at the action (``owner/repo``) level: action refs in the
  wild are tags/SHAs, not reliably semver, so a version-range comparison would be false-precise.
  We surface every advisory touching an action the fleet uses and let a human judge the version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from actionsplane.audit.pins import classify

_ACTIONS_ECOSYSTEM = "actions"


@dataclass(frozen=True, slots=True)
class AdvisoryRecord:
    ghsa_id: str
    package: str  # affected action, owner/repo
    severity: str
    summary: str
    url: str
    vulnerable_range: str | None
    first_patched: str | None
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdvisoryHit:
    advisory: AdvisoryRecord
    matched: str  # the exact uses: ref in the fleet that hit


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_patched(raw) -> str | None:
    # Global advisories return a string; some shapes nest it as {"identifier": "..."}.
    if isinstance(raw, dict):
        return raw.get("identifier")
    return raw or None


def parse_advisories(raw: list[dict]) -> list[AdvisoryRecord]:
    """Flatten a ``GET /advisories`` response into one record per affected actions-package.

    Advisories with no ``actions``-ecosystem vulnerability are dropped; an advisory affecting N
    actions yields N records (so package matching is a flat lookup).
    """
    out: list[AdvisoryRecord] = []
    for adv in raw or []:
        ghsa = adv.get("ghsa_id")
        if not ghsa:
            continue
        published = _parse_dt(adv.get("published_at"))
        severity = (adv.get("severity") or "unknown").lower()
        summary = (adv.get("summary") or "").strip()[:512]
        url = adv.get("html_url") or f"https://github.com/advisories/{ghsa}"
        for vuln in adv.get("vulnerabilities") or []:
            pkg = vuln.get("package") or {}
            if (pkg.get("ecosystem") or "").lower() != _ACTIONS_ECOSYSTEM:
                continue
            name = pkg.get("name")
            if not name:
                continue
            out.append(
                AdvisoryRecord(
                    ghsa_id=ghsa,
                    package=name,
                    severity=severity,
                    summary=summary,
                    url=url,
                    vulnerable_range=vuln.get("vulnerable_version_range"),
                    first_patched=_first_patched(vuln.get("first_patched_version")),
                    published_at=published,
                )
            )
    return out


def match_advisories(uses: list[str], advisories: list[AdvisoryRecord]) -> list[AdvisoryHit]:
    """Pure: which advisories name an action this workflow uses? One hit per advisory, with a
    sample of the ``uses:`` ref that matched. Case-insensitive on the ``owner/repo`` package."""
    # action owner/repo -> the raw ref, first occurrence wins for the sample.
    used: dict[str, str] = {}
    for ref in uses:
        action = classify(ref).action
        if action:
            used.setdefault(action.lower(), ref)
    hits: list[AdvisoryHit] = []
    for adv in advisories:
        ref = used.get(adv.package.lower())
        if ref is not None:
            hits.append(AdvisoryHit(adv, ref))
    return hits
