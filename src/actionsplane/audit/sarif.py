"""Emit ActionsPlane findings as SARIF 2.1.0 (plan §13 — the headline find→fix bridge).

GitHub Code Scanning consumes SARIF (https://docs.oasis-open.org/sarif/sarif/v2.1.0/). Uploading
our findings turns the Security tab into ActionsPlane's UI: each finding lands alongside zizmor's
and CodeQL's, dedup'd by ``partialFingerprints`` so we can update or close findings without
churning the alert list. The fingerprint we already store on every `Finding` (sha256 of
repo:path:type:ref — see `audit/findings.py`) is exactly what SARIF wants here.

Pure: this module takes ``Finding`` objects in and returns a SARIF dict. The upload is in
``github/client.upload_sarif`` so the emit logic is unit-testable without network.
"""

from __future__ import annotations

from collections.abc import Sequence

from actionsplane import __version__
from actionsplane.audit.findings import Finding
from actionsplane.models.enums import Severity

_SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/Schemata/sarif-schema-2.1.0.json"
)

# SARIF "level" maps from our severity. critical/high → error so they show as alerts;
# medium → warning; low/info → note (informational, doesn't gate merges by default).
_LEVEL = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
    Severity.INFO: "note",
}


def _result(finding: Finding) -> dict:
    location: dict = {}
    if finding.ref or finding.message:
        # Code Scanning requires a physicalLocation. Point at the finding's actual workflow file
        # when known (the SARIF service threads it in from the stored row); fall back to the
        # workflows dir only when path is absent. startLine 1 is a file-level anchor — the audit
        # engine doesn't track line numbers yet, and these policy findings apply to the whole file.
        location = {
            "physicalLocation": {
                "artifactLocation": {"uri": finding.path or ".github/workflows/"},
                "region": {"startLine": 1},
            }
        }
    out = {
        "ruleId": finding.finding_type.value,
        "level": _LEVEL.get(finding.severity, "warning"),
        "message": {"text": finding.message},
    }
    if location:
        out["locations"] = [location]
    # `partialFingerprints` is how Code Scanning dedups across runs. Reusing our existing
    # row fingerprint means an update emits the SAME id; closed-finding cleanup is automatic.
    # repo_id=0 here is fine: emit happens per-repo, so the caller's upload binds it to one repo.
    fp = finding.as_row(repo_id=0)["fingerprint"]
    out["partialFingerprints"] = {"actionsplanePrimary/v1": fp}
    return out


def _rule(finding_type: str) -> dict:
    return {
        "id": finding_type,
        "name": finding_type,
        "shortDescription": {"text": finding_type.replace("_", " ").title()},
        "defaultConfiguration": {"level": "warning"},
    }


def findings_to_sarif(findings: Sequence[Finding]) -> dict:
    """Build a SARIF 2.1.0 document with one ``run`` covering all findings."""
    rule_ids = sorted({f.finding_type.value for f in findings})
    return {
        "$schema": _SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "actionsplane",
                        "version": __version__,
                        "semanticVersion": __version__,
                        "informationUri": "https://github.com/itamar/actionsplane",
                        "rules": [_rule(r) for r in rule_ids],
                    }
                },
                "results": [_result(f) for f in findings],
            }
        ],
    }
