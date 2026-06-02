"""ActionsPlane CLI (plan §5.7) — for ops who live in the terminal.

    actionsplane status                         recent runs across watched repos
    actionsplane audit local [PATH]             audit every workflow in a local repo
    actionsplane audit all --file ci.yml        full audit report for a local workflow (works today)
    actionsplane audit pins --file ci.yml       classify every uses: by pin state
    actionsplane audit perms --file ci.yml      permission findings
    actionsplane audit pins --org foo           audit a whole org (phase 2, via API)
    actionsplane drift --template ci.yml        show drift
    actionsplane campaign create --op pin-shas  bulk operation
    actionsplane campaign status <id>           per-repo PR status

The ``audit ... --file`` commands run the parser + audit engine locally and are functional now;
``--org`` modes query the API and arrive with the rest of Phase 2.
"""

from __future__ import annotations

import pathlib
from collections import Counter

import httpx
import typer
from rich.console import Console
from rich.table import Table

from actionsplane import __version__
from actionsplane.audit import audit_permissions, audit_workflow, classify, parse_workflow
from actionsplane.audit.findings import Finding
from actionsplane.config import get_settings
from actionsplane.drift import compute_drift
from actionsplane.models.enums import PinState, Severity

app = typer.Typer(help="ActionsPlane — GitHub Actions control plane", no_args_is_help=True)
audit_app = typer.Typer(help="Audit workflows for supply-chain and hygiene issues")
campaign_app = typer.Typer(help="Create and track bulk-edit campaigns")
app.add_typer(audit_app, name="audit")
app.add_typer(campaign_app, name="campaign")

console = Console()

_SEVERITY_STYLE = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH: "red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
    Severity.INFO: "dim",
}
_SEVERITY_ORDER = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]


@app.command()
def version() -> None:
    """Print the ActionsPlane version."""
    console.print(f"actionsplane {__version__}")


@app.command()
def status(org: str | None = typer.Option(None, help="Limit to an org")) -> None:
    """Recent runs across all watched repos (queries the API)."""
    raise typer.Exit(_not_yet("status", "phase 1 (API client)"))


def _load_workflow(file: pathlib.Path):
    return parse_workflow(file.read_text(encoding="utf-8"), str(file))


def _render_findings(findings: list[Finding], title: str) -> None:
    if not findings:
        console.print(f"[green]✓ No findings — {title}[/]")
        return
    findings = sorted(findings, key=lambda f: _SEVERITY_ORDER.index(f.severity))
    table = Table(title=title)
    table.add_column("Severity")
    table.add_column("Type")
    table.add_column("Ref")
    table.add_column("Message")
    for f in findings:
        style = _SEVERITY_STYLE.get(f.severity, "")
        table.add_row(
            f"[{style}]{f.severity.value}[/]",
            f.finding_type.value,
            f.ref or "-",
            f.message,
        )
    console.print(table)
    console.print(f"[dim]{len(findings)} finding(s).[/]")


@audit_app.command("all")
def audit_all(
    file: pathlib.Path = typer.Option(..., help="Workflow YAML file to audit"),
    allowlist: str | None = typer.Option(None, help="Comma-separated trusted publishers"),
) -> None:
    """Run the full audit suite over a local workflow file."""
    wf = _load_workflow(file)
    pubs = {p.strip() for p in allowlist.split(",")} if allowlist else None
    _render_findings(audit_workflow(wf, publisher_allowlist=pubs), f"Audit — {file}")


@audit_app.command("pins")
def audit_pins(
    org: str | None = typer.Option(None, help="Audit a whole org via the API"),
    file: pathlib.Path | None = typer.Option(None, help="Classify a local workflow YAML file"),
) -> None:
    """Classify every ``uses:`` reference by pin state."""
    if file is None and org is None:
        console.print("[yellow]Provide --file <path> (local) or --org <name> (via API).[/]")
        raise typer.Exit(2)
    if org is not None:
        raise typer.Exit(_not_yet("audit pins --org", "phase 2 (API)"))

    wf = _load_workflow(file)
    refs = wf.all_uses()
    if not refs:
        console.print("[dim]No `uses:` references found.[/]")
        return
    table = Table(title=f"Pin audit — {file}")
    table.add_column("Action / ref")
    table.add_column("Pin state")
    for ref in refs:
        table.add_row(ref, classify(ref).pin_state.value)
    console.print(table)


@audit_app.command("perms")
def audit_perms(
    file: pathlib.Path | None = typer.Option(None, help="Audit a local workflow YAML file"),
    org: str | None = typer.Option(None, help="Audit a whole org via the API"),
) -> None:
    """Flag missing or over-broad ``permissions:`` blocks."""
    if file is None and org is None:
        console.print("[yellow]Provide --file <path> (local) or --org <name> (via API).[/]")
        raise typer.Exit(2)
    if org is not None:
        raise typer.Exit(_not_yet("audit perms --org", "phase 2 (API)"))
    _render_findings(audit_permissions(_load_workflow(file)), f"Permission audit — {file}")


def _discover_workflows(path: pathlib.Path) -> list[pathlib.Path]:
    """Find workflow YAMLs: ``<path>/.github/workflows/*`` or, failing that, treat ``path``
    itself as a workflows directory."""
    wf_dir = path / ".github" / "workflows"
    base = wf_dir if wf_dir.is_dir() else (path if path.is_dir() else None)
    if base is None:
        return []
    return sorted({*base.glob("*.yml"), *base.glob("*.yaml")})


@audit_app.command("local")
def audit_local(
    path: pathlib.Path = typer.Argument(
        pathlib.Path("."), help="Repo root (scans .github/workflows/) or a workflows directory"
    ),
    allowlist: str | None = typer.Option(None, help="Comma-separated trusted publishers"),
    exit_zero: bool = typer.Option(
        False, "--exit-zero", help="Always exit 0 (don't fail the command on findings)"
    ),
) -> None:
    """Audit every workflow file in a local repo — no GitHub, no API. CI/pre-commit friendly.

    Runs the full audit suite (pins, permissions, deprecation, publisher trust, concurrency)
    over each ``.github/workflows/*.yml`` and exits non-zero if anything is found (unless
    ``--exit-zero``), so it can gate a commit or a pipeline.
    """
    files = _discover_workflows(path)
    if not files:
        console.print(f"[yellow]No workflow files found under {path}.[/]")
        raise typer.Exit(1)

    pubs = {p.strip() for p in allowlist.split(",")} if allowlist else None
    total = 0
    by_sev: Counter[Severity] = Counter()
    for f in files:
        try:
            wf = _load_workflow(f)
        except Exception as exc:  # a malformed workflow is itself worth surfacing
            console.print(f"[red]✗ {f}: failed to parse ({exc})[/]")
            total += 1
            continue
        findings = audit_workflow(wf, publisher_allowlist=pubs)
        _render_findings(findings, str(f))
        total += len(findings)
        for fi in findings:
            by_sev[fi.severity] += 1

    summary = ", ".join(f"{by_sev[s]} {s.value}" for s in _SEVERITY_ORDER if by_sev[s])
    console.print(
        f"\n[bold]{total} finding(s) across {len(files)} file(s)[/]"
        + (f" ({summary})" if summary else "")
    )
    if total and not exit_zero:
        raise typer.Exit(1)


@app.command()
def drift(
    template: pathlib.Path = typer.Option(..., help="Canonical template workflow file"),
    against: pathlib.Path | None = typer.Option(
        None, help="Candidate workflow file to compare locally"
    ),
) -> None:
    """Show structural drift of a candidate workflow against a canonical template."""
    if against is None:
        raise typer.Exit(_not_yet("drift --template <name> (org mode)", "phase 3 (API)"))
    report = compute_drift(
        template.read_text(encoding="utf-8"),
        against.read_text(encoding="utf-8"),
        path=str(against),
    )
    color = "green" if not report.is_drifted else "yellow"
    console.print(f"Drift severity: [{color}]{report.severity.value}[/]")
    for change in report.changes:
        console.print(f"  • {change}")
    if not report.changes:
        console.print("[green]✓ identical[/]")


@campaign_app.command("preview")
def campaign_preview(
    op: str = typer.Option("pin-shas", help="Operation to preview"),
    file: pathlib.Path = typer.Option(..., help="Workflow file to preview the edit against"),
) -> None:
    """Local dry-run: show which action refs an operation would change in a workflow file.

    SHA resolution + the full diff + PR happen against the API/GitHub; this previews targets.
    """
    if op != "pin-shas":
        raise typer.Exit(_not_yet(f"campaign preview --op {op}", "phase 4"))
    wf = _load_workflow(file)
    targets = [
        ref
        for ref in wf.all_uses()
        if classify(ref).pin_state in (PinState.TAG_PINNED, PinState.BRANCH_PINNED)
    ]
    if not targets:
        console.print("[green]✓ Nothing to pin — all action refs are already SHA-pinned.[/]")
        return
    table = Table(title=f"pin-shas preview — {file}")
    table.add_column("Would pin")
    table.add_column("Current state")
    for ref in targets:
        table.add_row(ref, classify(ref).pin_state.value)
    console.print(table)
    console.print(f"[dim]{len(targets)} ref(s) would be pinned to commit SHAs.[/]")


@campaign_app.command("create")
def campaign_create(
    name: str = typer.Option(..., help="Campaign name"),
    op: str = typer.Option("pin-shas", help="Operation"),
    repos: str = typer.Option(..., help="Comma-separated repo IDs to target"),
) -> None:
    """Create a bulk-edit campaign via the API (computes dry-run diffs; apply is separate)."""
    repo_ids = [int(r) for r in repos.split(",") if r.strip()]
    base = get_settings().api_url.rstrip("/")
    resp = httpx.post(
        f"{base}/api/v1/campaigns",
        json={"name": name, "operation": op, "repo_ids": repo_ids},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    console.print(f"Campaign [bold]{data['id']}[/] ({data['status']}) created.")
    _print_targets(data.get("targets", []))


@campaign_app.command("status")
def campaign_status(campaign_id: int) -> None:
    """Show per-repo PR status for a campaign (via the API)."""
    base = get_settings().api_url.rstrip("/")
    resp = httpx.get(f"{base}/api/v1/campaigns/{campaign_id}", timeout=30)
    resp.raise_for_status()
    data = resp.json()
    console.print(f"Campaign [bold]{data['id']}[/] — {data['name']} ({data['status']})")
    _print_targets(data.get("targets", []))


def _print_targets(targets: list[dict]) -> None:
    if not targets:
        console.print("[dim]No targets.[/]")
        return
    table = Table()
    table.add_column("Repo ID")
    table.add_column("Status")
    table.add_column("PR")
    for t in targets:
        table.add_row(str(t["repo_id"]), t["status"], t.get("pr_url") or "-")
    console.print(table)


def _not_yet(cmd: str, phase: str) -> int:
    console.print(f"[yellow]`{cmd}` is not implemented yet (scheduled for {phase}).[/]")
    return 1


if __name__ == "__main__":
    app()
