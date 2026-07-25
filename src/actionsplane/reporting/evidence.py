"""Build a campaign's compliance evidence bundle (roadmap #8).

The bundle answers an auditor's question — *what did this campaign set out to fix, and did the fix
actually land?* — with three artifacts zipped together:

* ``report.html`` — a self-contained, printable report (inline CSS, no external assets) an auditor
  can read or "Print → Save as PDF".
* ``evidence.json`` — the same facts, machine-readable, for a compliance pipeline.
* ``manifest.txt`` — the SHA-256 of each artifact, so a stored bundle is tamper-evident.

Rendering is pure (``assemble_evidence`` → ``render_html`` / ``render_json`` / ``evidence_zip``) and
takes its timestamp as an argument, so the whole thing is deterministic and unit-testable without a
DB or a clock. ``build_campaign_evidence`` is the thin DB-facing orchestrator the endpoint calls.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from html import escape

from sqlalchemy.ext.asyncio import AsyncSession

from actionsplane import __version__
from actionsplane.db.repository import (
    campaign_finding_details,
    campaign_verification,
    list_repos,
    list_targets,
)

# Human labels for the finding types a campaign can target (mirrors the UI's operationLabel intent).
_TYPE_LABELS: dict[str, str] = {
    "unpinned_action": "Unpinned action",
    "missing_permissions": "Missing permissions",
    "broad_permissions": "Broad permissions",
    "missing_concurrency": "Missing concurrency",
    "deprecated_action": "Deprecated action",
}


def _type_label(t: str) -> str:
    return _TYPE_LABELS.get(t, t.replace("_", " ").capitalize())


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


@dataclass(frozen=True)
class EvidenceTarget:
    repo: str
    status: str
    pr_number: int | None
    pr_url: str | None


@dataclass(frozen=True)
class EvidenceFinding:
    repo: str
    finding_type: str
    severity: str
    path: str | None
    ref: str | None
    message: str | None
    first_seen_at: datetime | None
    resolved_at: datetime | None
    resolved: bool


@dataclass(frozen=True)
class EvidenceBundle:
    """Everything the report/JSON need, normalized and repo-name-resolved."""

    campaign_id: int
    name: str
    operation: str
    status: str
    created_at: datetime | None
    generated_at: datetime
    tool_version: str
    targeted: int
    resolved: int
    by_type: dict[str, dict[str, int]]
    targets: list[EvidenceTarget] = field(default_factory=list)
    findings: list[EvidenceFinding] = field(default_factory=list)

    @property
    def resolved_pct(self) -> int:
        return round(self.resolved / self.targeted * 100) if self.targeted else 0


def assemble_evidence(
    *,
    campaign,
    targets: list,
    verification: dict,
    finding_details: list[dict],
    repo_names: dict[int, str],
    generated_at: datetime,
) -> EvidenceBundle:
    """Pure assembly: normalize raw DB rows into an :class:`EvidenceBundle` (no I/O, no clock)."""

    def name_of(repo_id: int) -> str:
        return repo_names.get(repo_id, f"#{repo_id}")

    ev_targets = [
        EvidenceTarget(
            repo=name_of(t.repo_id),
            status=t.status,
            pr_number=t.pr_number,
            pr_url=t.pr_url,
        )
        for t in targets
    ]
    ev_findings = [
        EvidenceFinding(
            repo=name_of(f["repo_id"]),
            finding_type=f["finding_type"],
            severity=f["severity"],
            path=f.get("path"),
            ref=f.get("ref"),
            message=f.get("message"),
            first_seen_at=f.get("first_seen_at"),
            resolved_at=f.get("resolved_at"),
            resolved=bool(f.get("resolved")),
        )
        for f in finding_details
    ]
    return EvidenceBundle(
        campaign_id=campaign.id,
        name=campaign.name,
        operation=campaign.operation,
        status=campaign.status,
        created_at=campaign.created_at,
        generated_at=generated_at,
        tool_version=__version__,
        targeted=int(verification.get("targeted", 0)),
        resolved=int(verification.get("resolved", 0)),
        by_type=verification.get("by_type", {}),
        targets=ev_targets,
        findings=ev_findings,
    )


def render_json(bundle: EvidenceBundle) -> dict:
    """The machine-readable evidence payload."""
    return {
        "schema": "actionsplane.evidence/v1",
        "tool": {"name": "ActionsPlane", "version": bundle.tool_version},
        "generated_at": _iso(bundle.generated_at),
        "campaign": {
            "id": bundle.campaign_id,
            "name": bundle.name,
            "operation": bundle.operation,
            "status": bundle.status,
            "created_at": _iso(bundle.created_at),
        },
        "verification": {
            "targeted": bundle.targeted,
            "resolved": bundle.resolved,
            "resolved_pct": bundle.resolved_pct,
            "by_type": bundle.by_type,
        },
        "targets": [
            {
                "repo": t.repo,
                "status": t.status,
                "pr_number": t.pr_number,
                "pr_url": t.pr_url,
            }
            for t in bundle.targets
        ],
        "findings": [
            {
                "repo": f.repo,
                "finding_type": f.finding_type,
                "severity": f.severity,
                "path": f.path,
                "ref": f.ref,
                "message": f.message,
                "first_seen_at": _iso(f.first_seen_at),
                "resolved_at": _iso(f.resolved_at),
                "resolved": f.resolved,
            }
            for f in bundle.findings
        ],
    }


_CSS = """
:root { --ink:#16181d; --muted:#5b616e; --line:#e4e6ea; --ok:#137a53; --open:#b23b2e;
        --accent:#c9770f; --chip:#f3f4f6; }
* { box-sizing:border-box; }
body { margin:0; background:#fff; color:var(--ink); font:14px/1.55 -apple-system,Segoe UI,Roboto,
       Helvetica,Arial,sans-serif; }
.wrap { max-width:900px; margin:0 auto; padding:40px 32px 64px; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
header { border-bottom:2px solid var(--accent); padding-bottom:18px; margin-bottom:24px; }
.eyebrow { text-transform:uppercase; letter-spacing:.12em; font-size:11px; font-weight:700;
           color:var(--accent); }
h1 { font-size:24px; margin:6px 0 4px; }
.meta { color:var(--muted); font-size:12.5px; }
.headline { display:flex; gap:28px; align-items:baseline; margin:22px 0 4px; }
.big { font-size:40px; font-weight:800; letter-spacing:-.02em; line-height:1; }
.big small { font-size:18px; font-weight:600; color:var(--muted); }
.bar { height:10px; border-radius:6px; background:var(--chip); overflow:hidden; margin:14px 0 28px }
.bar > span { display:block; height:100%; background:var(--ok); }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
     border-bottom:1px solid var(--line); padding-bottom:6px; margin:32px 0 12px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.05em;
     color:var(--muted); padding:6px 10px; border-bottom:1px solid var(--line); }
td { padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
tr:last-child td { border-bottom:none; }
.pill { display:inline-block; font-size:11px; font-weight:600; padding:1px 8px; border-radius:20px;
        background:var(--chip); }
.pill.ok { background:#e2f3ec; color:var(--ok); }
.pill.open { background:#fbe9e6; color:var(--open); }
.pill.sev-high,.pill.sev-critical { background:#fbe9e6; color:var(--open); }
.right { text-align:right; }
a { color:var(--accent); }
footer { margin-top:40px; padding-top:14px; border-top:1px solid var(--line); color:var(--muted);
         font-size:11.5px; }
@media print { .wrap { padding:0; } a { color:var(--ink); text-decoration:none; } }
"""


def _pill(text: str, cls: str = "") -> str:
    return f'<span class="pill {cls}">{escape(text)}</span>'


def render_html(bundle: EvidenceBundle) -> str:
    """A self-contained, printable HTML report (inline CSS, no external requests)."""
    b = bundle
    created = b.created_at.strftime("%Y-%m-%d %H:%M UTC") if b.created_at else "—"
    generated = b.generated_at.strftime("%Y-%m-%d %H:%M UTC")

    # By-type rows
    type_rows = (
        "".join(
            f"<tr><td>{escape(_type_label(t))}</td>"
            f'<td class="right mono">{c.get("resolved", 0)}/{c.get("targeted", 0)}</td></tr>'
            for t, c in sorted(b.by_type.items())
        )
        or '<tr><td colspan="2" class="meta">No finding-backed targets for this op.</td></tr>'
    )

    # Targets (PRs) rows
    def target_row(t: EvidenceTarget) -> str:
        pr = (
            f'<a href="{escape(t.pr_url)}">#{t.pr_number}</a>'
            if t.pr_url
            else '<span class="meta">—</span>'
        )
        return (
            f"<tr><td class='mono'>{escape(t.repo)}</td>"
            f"<td>{_pill(t.status)}</td><td>{pr}</td></tr>"
        )

    target_rows = "".join(target_row(t) for t in b.targets) or (
        '<tr><td colspan="3" class="meta">No targets.</td></tr>'
    )

    # Findings rows
    def finding_row(f: EvidenceFinding) -> str:
        state = _pill("Resolved", "ok") if f.resolved else _pill("Open", "open")
        loc = escape(f.path or "—")
        if f.ref:
            loc += f' <span class="meta mono">{escape(f.ref)}</span>'
        return (
            f"<tr><td class='mono'>{escape(f.repo)}</td>"
            f"<td>{escape(_type_label(f.finding_type))}</td>"
            f"<td>{_pill(f.severity, f'sev-{escape(f.severity)}')}</td>"
            f"<td>{loc}</td><td>{state}</td></tr>"
        )

    finding_rows = "".join(finding_row(f) for f in b.findings) or (
        '<tr><td colspan="5" class="meta">No findings were snapshotted for this campaign.</td></tr>'
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ActionsPlane evidence — {escape(b.name)}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<header>
  <div class="eyebrow">ActionsPlane · Compliance evidence</div>
  <h1>{escape(b.name)}</h1>
  <div class="meta">Campaign #{b.campaign_id} · operation
   <span class="mono">{escape(b.operation)}</span>
   · status {escape(b.status)} · created {escape(created)}</div>
</header>

<div class="headline">
  <div class="big">{b.resolved}<small>/{b.targeted} resolved</small></div>
  <div class="meta">Findings this campaign targeted that a re-audit confirms are now
   resolved ({b.resolved_pct}%).</div>
</div>
<div class="bar"><span style="width:{b.resolved_pct}%"></span></div>

<h2>Resolution by finding type</h2>
<table><thead><tr><th>Finding type</th><th class="right">Resolved / targeted</th></tr></thead>
<tbody>{type_rows}</tbody></table>

<h2>Pull requests ({len(b.targets)})</h2>
<table><thead><tr><th>Repository</th><th>Status</th><th>PR</th></tr></thead>
<tbody>{target_rows}</tbody></table>

<h2>Targeted findings ({len(b.findings)})</h2>
<table><thead><tr><th>Repository</th><th>Type</th><th>Severity</th><th>Location</th><th>State</th></tr>
</thead><tbody>{finding_rows}</tbody></table>

<footer>
  Generated {escape(generated)} by ActionsPlane v{escape(b.tool_version)}.
  Integrity: verify each file against <span class="mono">manifest.txt</span> (SHA-256).
  "Resolved" means a re-audit no longer observes the finding — evidence the fix landed, not merely
  that a PR opened.
</footer>
</div></body></html>
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def evidence_zip(bundle: EvidenceBundle) -> bytes:
    """Zip ``report.html`` + ``evidence.json`` + a SHA-256 ``manifest.txt`` into bytes.

    Deterministic: entry timestamps come from ``bundle.generated_at``, not the wall clock, so the
    same bundle always produces byte-identical output (testable, reproducible archives).
    """
    html = render_html(bundle).encode("utf-8")
    payload = json.dumps(render_json(bundle), indent=2, sort_keys=True).encode("utf-8")
    manifest = (
        "SHA-256 manifest — ActionsPlane evidence bundle\n"
        f'campaign: {bundle.campaign_id} "{bundle.name}"\n'
        f"generated: {_iso(bundle.generated_at)}\n\n"
        f"{_sha256(html)}  report.html\n"
        f"{_sha256(payload)}  evidence.json\n"
    ).encode()

    dt = bundle.generated_at.timetuple()[:6]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in (
            ("report.html", html),
            ("evidence.json", payload),
            ("manifest.txt", manifest),
        ):
            info = zipfile.ZipInfo(name, date_time=dt)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


async def build_campaign_evidence(
    session: AsyncSession, campaign, *, generated_at: datetime
) -> EvidenceBundle:
    """DB-facing orchestrator: gather a campaign's data and assemble its evidence bundle."""
    targets = await list_targets(session, campaign_id=campaign.id)
    verification = await campaign_verification(session, campaign.id)
    finding_details = await campaign_finding_details(session, campaign.id)
    repo_names = {
        r.id: f"{r.owner}/{r.name}" for r in await list_repos(session, watched_only=False)
    }
    return assemble_evidence(
        campaign=campaign,
        targets=targets,
        verification=verification,
        finding_details=finding_details,
        repo_names=repo_names,
        generated_at=generated_at,
    )
