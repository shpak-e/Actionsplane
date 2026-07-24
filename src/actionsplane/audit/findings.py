"""The Finding value object produced by the audit engine.

Pure data — no ORM, no I/O. The persistence layer maps these onto the ``audit_findings`` table
(``db.models.AuditFinding``); the CLI/UI render them directly. Keeping findings decoupled from
storage lets the whole engine run and be tested in-process over a parsed workflow.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from actionsplane.models.enums import FindingType, Severity


@dataclass(frozen=True, slots=True)
class Finding:
    finding_type: FindingType
    severity: Severity
    message: str
    ref: str | None = None  # the `uses:` string, job id, or step locus the finding is about
    # Workflow file the finding is in. The audit engine leaves this None (it supplies the path at
    # ``as_row`` time); the SARIF path sets it from the stored row so alerts point at the real file
    # rather than a placeholder directory. Not part of the fingerprint.
    path: str | None = None

    def as_row(
        self, *, repo_id: int, path: str | None = None, workflow_id: int | None = None
    ) -> dict:
        """Shape this finding for an ``audit_findings`` upsert (timestamps added by the caller)."""
        return {
            "repo_id": repo_id,
            "workflow_id": workflow_id,
            "path": path,
            "finding_type": self.finding_type.value,
            "severity": self.severity.value,
            "ref": self.ref,
            "message": self.message,
            "fingerprint": fingerprint(repo_id, path, self.finding_type.value, self.ref),
        }


def fingerprint(repo_id: int, path: str | None, finding_type: str, ref: str | None) -> str:
    """Stable dedup key for a finding. Same logical finding -> same fingerprint across re-audits."""
    key = f"{repo_id}:{path or ''}:{finding_type}:{ref or ''}"
    return hashlib.sha256(key.encode()).hexdigest()
