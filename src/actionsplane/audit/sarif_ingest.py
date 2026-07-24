"""Ingest external SARIF into ActionsPlane findings (D1 — zizmor orchestration).

The emit side (``audit/sarif.py``) pushes our findings *out* to Code Scanning; this is the reverse:
pull a scanner's SARIF *in* so zizmor's ~38 rules — and poutine / octoscan / Scorecard, which all
speak SARIF — become ActionsPlane findings without re-implementing a single rule (the decision-log
call: orchestrate, don't rebuild). Pure: SARIF dict in, finding rows out; the service persists them.

Ingested finding types are namespaced by tool (``zizmor:template-injection``) so they never collide
with native ActionsPlane types and can be lifecycle-managed per source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from actionsplane.audit.findings import fingerprint
from actionsplane.models.enums import Severity

# SARIF result.level -> our Severity. `security-severity` (a CVSS-style float some tools attach in
# result.properties) overrides this upward when present, matching how Code Scanning ranks them.
_LEVEL = {
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "note": Severity.LOW,
    "none": Severity.INFO,
}


def _severity(result: dict, rule_levels: dict[str, str]) -> Severity:
    sec = (result.get("properties") or {}).get("security-severity")
    if sec is not None:
        try:
            score = float(sec)
            if score >= 9.0:
                return Severity.CRITICAL
            if score >= 7.0:
                return Severity.HIGH
            if score >= 4.0:
                return Severity.MEDIUM
            return Severity.LOW
        except (TypeError, ValueError):
            pass
    level = result.get("level") or rule_levels.get(result.get("ruleId", ""), "warning")
    return _LEVEL.get(level, Severity.MEDIUM)


def _location(result: dict) -> tuple[str | None, int | None]:
    locs = result.get("locations") or []
    if not locs:
        return None, None
    phys = (locs[0] or {}).get("physicalLocation") or {}
    uri = (phys.get("artifactLocation") or {}).get("uri")
    line = (phys.get("region") or {}).get("startLine")
    return uri, (line if isinstance(line, int) else None)


@dataclass(frozen=True, slots=True)
class IngestedFinding:
    finding_type: str  # "tool:ruleId"
    severity: Severity
    path: str | None
    line: int | None
    message: str
    rule_id: str

    def as_row(self, *, repo_id: int) -> dict[str, Any]:
        # Encode the line into ref so two hits of the same rule at different lines in one file are
        # distinct findings (distinct fingerprints), not collapsed into one.
        ref = f"{self.rule_id}@L{self.line}" if self.line is not None else self.rule_id
        return {
            "repo_id": repo_id,
            "workflow_id": None,
            "path": self.path,
            "finding_type": self.finding_type,
            "severity": self.severity.value,
            "ref": ref,
            "message": self.message,
            "fingerprint": fingerprint(repo_id, self.path, self.finding_type, ref),
        }


def _rule_default_levels(run: dict) -> dict[str, str]:
    """Map ruleId -> its defaultConfiguration.level, for results that omit an explicit level."""
    levels: dict[str, str] = {}
    for rule in ((run.get("tool") or {}).get("driver") or {}).get("rules") or []:
        rid = rule.get("id")
        lvl = (rule.get("defaultConfiguration") or {}).get("level")
        if rid and lvl:
            levels[rid] = lvl
    return levels


def parse_sarif(doc: dict, *, tool_override: str | None = None) -> list[IngestedFinding]:
    """Parse a SARIF 2.1.0 document into ingestable findings (one per result)."""
    out: list[IngestedFinding] = []
    for run in doc.get("runs") or []:
        driver = (run.get("tool") or {}).get("driver") or {}
        tool = (tool_override or driver.get("name") or "external").strip().lower()
        rule_levels = _rule_default_levels(run)
        for result in run.get("results") or []:
            rule_id = result.get("ruleId") or "unknown"
            uri, line = _location(result)
            message = ((result.get("message") or {}).get("text") or "").strip() or rule_id
            out.append(
                IngestedFinding(
                    finding_type=f"{tool}:{rule_id}",
                    severity=_severity(result, rule_levels),
                    path=uri,
                    line=line,
                    message=message,
                    rule_id=rule_id,
                )
            )
    return out


def tools_in(doc: dict) -> set[str]:
    """The distinct tool names a SARIF document reports (for source-scoped lifecycle)."""
    return {
        ((run.get("tool") or {}).get("driver") or {}).get("name", "external").strip().lower()
        for run in doc.get("runs") or []
    }
