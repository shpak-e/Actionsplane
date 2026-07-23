<div align="center">

# ActionsPlane

**The fleet control plane for GitHub Actions.**
Observe, audit, and fix workflows across hundreds of repositories — from one self-hosted UI, API, and CLI.
Every fix ships as a reviewable pull request.

[![CI](https://github.com/shpak-e/Actionsplane/actions/workflows/ci.yml/badge.svg)](https://github.com/shpak-e/Actionsplane/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![Status: preview](https://img.shields.io/badge/status-preview-orange)

</div>

> [!WARNING]
> **🚧 Preview — not production-ready.** ActionsPlane has not yet been validated against a real GitHub org
> end-to-end. Do not point it at production repositories. Evaluate in a sandbox only; interfaces and schema may
> change without notice.

---

## Why

If your team owns 30+ repositories, GitHub Actions gives you no fleet view. You get:

- **Status fragmentation** — N browser tabs to know if builds are green.
- **Supply-chain blindness** — unpinned actions and over-broad `GITHUB_TOKEN` scopes, invisible until an incident (tj-actions, trivy-action…).
- **Workflow drift** — the same workflow copy-pasted everywhere, slowly diverging.
- **Fix toil** — every deprecation or pinning push means N checkouts, N edits, N PRs, by hand.

Existing tools each cover a fragment: scanners find problems in one repo, bots bump versions, dashboards show metrics. ActionsPlane combines **observe + audit + fix** in one loop — and every change it makes lands as a pull request, never a push.

## What it does

**🔭 Observe** — a GitHub App streams `workflow_run`/`workflow_job` webhooks into Postgres (HMAC-verified, deduped, with a polling reconciler as safety net). One dashboard shows live runs across every repo, with per-workflow metrics (success rate, p50/p95 duration, flakiness) and a **Pipelines** graph that maps cross-repo trigger chains (`workflow_run`, reusable workflows, dispatch) — down to the failing step of a broken pipeline.

**🔍 Audit** — workflows are parsed into a typed AST and checked continuously: unpinned actions, missing/over-broad `permissions:`, deprecated actions, missing `concurrency:`, unverified publishers. Findings have a lifecycle, roll up into a per-repo posture scorecard, and upload as SARIF to GitHub Code Scanning (resolved findings close their alerts).

**📐 Drift** — register a canonical workflow template, bind repos to it, and get structural AST diffs (identical → minor → content → structural) instead of noisy text diffs. See exactly which of 120 repos diverged from the golden release workflow, and how badly.

**🔧 Fix** — bulk **campaigns**: pick an operation (e.g. *pin every action to its commit SHA*), preview a per-repo **dry-run diff**, then apply — ActionsPlane opens a branch + PR per repo with comment- and format-preserving YAML edits (`ruamel` round-trip). Apply is double-gated (opt-in flag + operate token), reuses the dry-run-resolved SHAs so the reviewed diff is exactly what lands, and every write is recorded in an append-only audit log.

**No GitHub App? Offline mode** pulls workflows/runs for any list of public repos over the public API — full dashboard, no webhooks. And `actionsplane audit local .` scans a local checkout as a CI gate (non-zero exit on findings).

## Quickstart

```sh
# full local stack: Postgres + Redis + API + ingestor + worker, auto-migrated
docker compose -f deploy/docker-compose.full.yml up --build

open http://localhost:3001        # dashboard
open http://localhost:8000/docs   # API

# demo data, no GitHub App needed:
PYTHONPATH=src python scripts/seed_local.py
```

Or for development:

```sh
make install   # uv sync
make up        # Postgres + Redis
make migrate && make test && make lint
make api       # + make ingestor / make worker (host-run, fast reload)
```

To ingest **real repositories**, create a GitHub App and install it on the repos you want (permissions and webhook setup are documented in `.env.example`) — or use offline mode for public repos, no App needed.

```sh
# CLI highlights
actionsplane audit local .                          # scan a local repo, CI-gate friendly
actionsplane audit all                              # fleet audit via the API
actionsplane campaign preview --op pin-shas --file  # what would change, before anything changes
actionsplane campaign create / status               # run the campaign
```

## Deploy

Docker Compose (dev + full stacks), **Kubernetes** via kustomize (`deploy/k8s/`) or **Helm** (`deploy/helm/actionsplane/`). Ships hardened by default: non-root read-only containers, default-deny NetworkPolicies, Redis auth, ingress TLS, two-tier API tokens (operate/read-only).

## How it's built

FastAPI + SQLAlchemy (async) + Postgres + Redis/arq worker; React + Vite + TanStack Query frontend with SSE live updates; Typer CLI; optional OpenTelemetry tracing end-to-end. ~240 hermetic tests incl. hypothesis property tests on the YAML-edit engine.

**Design principles:** GitHub App, never PATs · webhooks first, polling only as reconciliation · **all edits via PRs**, never a direct push · AST, not regex — edits preserve comments and formatting · fail-closed write gates · self-hosted, no SaaS dependency.

## Project status & roadmap

v1 feature-complete and hardened in a local sandbox; **live validation against a real GitHub org is the current milestone**. After that: a policy-readiness simulator for GitHub's new enforcement knobs, deprecation-radar migration campaigns, lockfile campaigns, and zizmor-orchestrated fix campaigns.

## Contributing

Issues and PRs welcome once the project exits preview. Until then: `make test && make lint` must pass; the CI dogfoods ActionsPlane's own rules (all third-party actions SHA-pinned).

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
