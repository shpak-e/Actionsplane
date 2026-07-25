"""Compliance evidence export (roadmap #8).

Turns a campaign's verified-closed data into a self-contained, tamper-evident bundle: a printable
HTML report + a machine-readable JSON + a SHA-256 manifest, zipped with the stdlib (no new deps).
"""

from actionsplane.reporting.evidence import (
    EvidenceBundle,
    assemble_evidence,
    build_campaign_evidence,
    evidence_zip,
    render_html,
    render_json,
)

__all__ = [
    "EvidenceBundle",
    "assemble_evidence",
    "build_campaign_evidence",
    "evidence_zip",
    "render_html",
    "render_json",
]
