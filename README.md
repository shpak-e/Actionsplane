# ActionsPlane

> A self-hosted, OSS control plane for **observing, auditing, and editing** GitHub Actions across many repositories from a single UI/API/CLI.

> [!WARNING]
> **🚧 Work in progress — testing/preview, NOT a production-ready product.**
> ActionsPlane is under active development and has not yet been validated against a real GitHub org end-to-end. Interfaces, schema, and behaviour may change without notice. **Do not run this against production repositories or rely on it for security-critical workflows.** Use at your own risk, in a sandbox, for evaluation only.

Teams that own many repos juggle four problems no single OSS tool solves end-to-end: status fragmentation (N tabs to see if builds are green), workflow drift (the same workflow copy-pasted everywhere slowly diverges), supply-chain blindness (unpinned actions, over-broad `GITHUB_TOKEN` scopes), and no cross-repo metrics (which repo burns the most minutes? which workflow is flakiest?). ActionsPlane combines the **observe + audit + edit** triangle that existing tools only cover in fragments — and does all edits safely, through PRs.

See [`plan.md`](plan.md) for the full design rationale, differentiation, and phased roadmap, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the runtime view.

## Status

**⚠️ Testing / preview — not production-ready.** Functionally complete in a local sandbox, but still pending live validation against a real GitHub org (see end of this section). Treat everything below as "works in dev", not "ready to deploy".

**Phases 1–4 functional (v1 feature-complete), v1.1 hardening done, Phase 5 trust hardening done except live validation, + GitLab provider started (v2). 157 tests green; ruff-clean; 10 Alembic migrations.** The docker stack + schema are validated against a real Postgres locally (2026-06-01), and the **React dashboard** (redesigned — Runs / Security / Drift / **Pipelines**, deep links to GitHub Actions, run drawer with job logs + re-run) builds and runs live against the API in `docker-compose.full.yml` (frontend on :3001). The **Pipelines** tab maps the fleet-wide cross-workflow trigger graph (`workflow_run` chains, reusable-workflow calls, cross-repo PR/dispatch) as a **layered left→right flow graph** — repo-coloured node cards, typed/curved connectors, precise-vs-heuristic edges. Each node shows its **latest-run status** and, when a pipeline failed, **the job/step that failed** (e.g. `Deploy → terraform apply`); the run drawer renders a **per-job step tree** highlighting the failing step. Two ways to populate it: a **GitHub App** (live webhooks), or **offline mode** — point it at a list of public repos and it pulls their workflows/runs over the public API with a Sync button, no App needed. The full live ingest→PR loop against a real GitHub org is still pending (next live validation). **See [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)** to run it locally, view the UI, and add repos.

**v1.1 hardening shipped (this pass):** Link-header **pagination** for `list_workflow_runs`/`list_workflow_files` (bounded by `max_runs`, truncation logged not silent); **SARIF orchestration end-to-end** — `POST /repos/{id}/sarif/upload` + a post-audit worker step push findings to Code Scanning, gated by `security_events_enabled` (empty result-sets upload too, so resolved findings close their alerts); **SSE disconnect-safe** stream (bus subscription `aclose`'d in a `finally` + keep-alive `ping`, so a closed tab can't strand a Redis connection); **OpenTelemetry tracing** wired as one distributed trace across ingest → worker → audit → SARIF (W3C context carried over the arq queue; off by default, import-safe); **hypothesis property tests** pinning the pin-classifier and the SHA-pinning edit (idempotent, comment-preserving, never emits un-parseable YAML).

- **Observe (P1 ✅):** HMAC webhook ingest → arq worker → idempotent upserts; GitHub App auth; reconcile cron; REST read API + live SSE; metrics; React dashboard; Alembic schema.
- **Audit (P2 ✅):** AST parser + pure rule engine (pin / permissions / deprecation / publisher / concurrency); org-wide audit service with finding lifecycle; findings + posture-scorecard API; Security tab.
- **Drift (P3 ✅):** structural AST diff (identical → minor → content → structural); templates + bindings; filename autobind; drift sweep; Drift tab.
- **Edit (P4 ✅):** `campaigns` + `campaign_targets`; the **pin-shas** operation (ruamel round-trip, comment/format-preserving, pure given a resolver); GitHub write client (resolve SHA → branch → commit → PR); campaign **dry-run → apply** orchestration (per-target status; apply gated by `bulk_edits_enabled`); API `POST /campaigns`, `/campaigns/{id}/apply`, CLI `campaign create|status|preview`. The dry-run→PR flow is tested end-to-end against a mock GitHub.

**Hardening (three review passes):** API bearer-token auth; findings filtered in SQL + indexed; **bounded-concurrency** org sweeps with **expiry-aware, race-free token cache** (per-installation lock); **ETag/304 conditional requests + `Retry-After` backoff**; ingestor **`X-GitHub-Delivery` dedup** + body-size cap; **SARIF emit + Code Scanning upload** (the find→fix bridge — emit side wired); **out-of-order webhook upsert guard** (a late `in_progress` can't regress a stored `completed` run); **apply** reuses the dry-run-resolved SHAs (a tag retargeted between preview and apply can't change what lands), is fail-closed (needs `api_token`), and uses retry-safe branches. **GitLab provider (v2) started:** `.gitlab-ci.yml` parser + include/component pin audit behind a `Provider` protocol. Reviews + research: `docs/staff-review.md`, `docs/review-findings-2.md`, `docs/directions-research.md`. Full handoff + backlog: `docs/memory.md`. Remaining: live validation against a real org (install the App; smoke-test ingest → audit → SARIF → campaign dry-run, which produces the write-path HTTP cassette corpus).

CLI: `audit all|pins|perms --file`, `drift --template <a> --against <b>`, `campaign preview --op pin-shas --file`, `campaign create|status` (via API).

**Phase 5 trust hardening shipped (2026-07, except live validation):** append-only **write-operation audit log** (every mutating endpoint + worker SARIF step; `GET /api/v1/audit-log`) with **two-tier RBAC** (operate vs. read-only bearer token, fail-closed); **sweep leases** (portable atomic lease table — `worker replicas > 1` can no longer double-audit/double-PR); **job-upsert ordering gate** (SQL status-rank `CASE`, same guard class as the 0008 run fix); **per-install rate-limit budget** (`X-RateLimit-*` tracking + configurable floor; sweeps pause gracefully and resume next tick); **retention pruning cron** (raw webhook payloads nulled after N days keeping normalized history, delivery-dedup rows expired; batched, lease-guarded). Plus a **UI renovation**: warm-paper Claude-style design language (terracotta accent, serif display, soft status tints, recoloured pipeline graph).

**Next (v2 roadmap, 2026-07):** Phase 5.1 — **live validation** against a real org (the remaining blocker); then Phase 6 — the **policy-era wedge**: policy-readiness simulator, lockfile campaigns, SARIF ingest→converge-PRs. Full phased plan: [`plan.md` §14](plan.md); rationale: [`docs/architect-review-2026-07.md`](docs/architect-review-2026-07.md).

## Quickstart

```sh
make install        # uv sync (creates the venv, installs dev extras)
make up             # start Postgres + Redis (docker compose)
make migrate        # apply Alembic migrations
make test           # run the test suite (157 passing, hermetic)
make lint           # ruff check + format check

# full sandbox (Postgres + Redis + API + ingestor + worker, auto-migrated):
#   docker compose -f deploy/docker-compose.full.yml up --build
#   curl localhost:8000/healthz   •   open localhost:8000/docs

# populate demo data without a GitHub App (so the API/UI aren't empty):
PYTHONPATH=src python scripts/seed_local.py
curl localhost:8000/api/v1/repos

# Kubernetes: kustomize (deploy/k8s/) or Helm (deploy/helm/actionsplane/); per-component
#   Dockerfiles in deploy/docker/. Topology: docs/k8s-architecture.md

# scan a whole local repo's workflow files (no GitHub, CI-gate friendly — exits non-zero on findings):
uv run actionsplane audit local .
```

**→ Full walkthrough — running locally, seeding data, and adding real repos via the GitHub App
— is in [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md).**

Local dev config lives in `.env` (copy from `.env.example`). Services run on the host with `make api` / `make ingestor` / `make worker` for fast reload.

## Layout

```
actionsplane/
├── plan.md                     full design + roadmap
├── docs/ARCHITECTURE.md        runtime/data-flow view
├── src/actionsplane/
│   ├── config.py               env-driven settings (pydantic-settings)
│   ├── models/                 typed workflow AST (Workflow/Job/Step) + enums
│   ├── ingestor/               FastAPI webhook receiver + HMAC verification
│   ├── sync/                   async worker (event processing + polling reconcile)
│   ├── executor/               campaign orchestration + service (dry-run→PR) + operations
│   ├── api/                    REST + GraphQL read model for the UI/CLI
│   ├── drift/                  AST diff engine + drift service (severity ladder)
│   ├── audit/                  parser + rule engine + service + posture scorecard
│   ├── metrics/                pure metric functions (success/p50/p95/queue/flake)
│   ├── events/                 live event bus (Redis pub/sub → SSE)
│   ├── github/                 GitHub App auth + REST read/write client + factory
│   ├── providers/              provider seam + GitLab CI parser/audit (v2)
│   ├── observability/          OpenTelemetry tracing (optional, import-safe)
│   └── cli/                    `actionsplane` Typer CLI
├── frontend/                   React + Vite + TanStack Query dashboard (builds + runs; SSE live updates)
├── migrations/                 Alembic env + 9 schema migrations
├── deploy/                     docker-compose dev + full stacks, Dockerfiles, k8s + Helm
├── scripts/seed_local.py       seed a demo installation/repos/runs for local testing
├── docs/USER_GUIDE.md          run it locally, seed data, add repos via the GitHub App
├── tests/                      129 tests (pins, parser, audit, scorecard, drift, relations, operations, property[hypothesis], campaign svc, gitlab, factory, api-auth, api-endpoints-db, rerun, offline, cli-local, signature, auth, events/bus, metrics, client[+pagination], sarif, sarif-service, tracing, etag/backoff, ingestor-hardening, run-ordering)
└── .github/workflows/ci.yml    lint + test; third-party actions SHA-pinned (dogfooding)
```

## Roadmap

| Phase | Goal | Shippable artifact |
|---|---|---|
| 1 — Foundation & Observe ✅ | Dashboard of runs across many repos, with history | GitHub App + webhook ingestor + Postgres + REST + React grid |
| 2 — Audit ✅ | Surface every security/hygiene problem | Pin / publisher / permission / deprecation audits + CLI |
| 3 — Drift ✅ | Detect divergence from canonical workflows | Template registry + AST diff + drift dashboard |
| 4 — Edit ✅ | Safe bulk operations via PRs | Campaign engine + dry-run/diff + auto-merge |
| 5 — Live validation & trust (5.2–5.6 ✅) | Proven against a real org; safe to hold `contents:write` | ✅ write audit log + RBAC, sweep leases, job-ordering gate, rate-limit budget, retention pruning · ⏳ live ingest→audit→SARIF→PR loop + cassette corpus |
| 6 — Policy-era wedge | Remediation engine for GitHub's native enforcement | Policy-readiness simulator → fix campaigns, lockfile campaigns, SARIF ingest→converge-PRs |
| 7 — Observe v2 | Consume GitHub's new telemetry, not compete with it | Actions Data Stream adapter, metric rollups+retention, Renovate coexistence, Pipelines job-DAG |
| 8 — Enterprise & breadth | Wider deployment surface | Scoped-secrets migrations, GHES, LLM-assisted remediation (gated), MCP server, GitLab pillars |

**Positioning (2026-07):** GitHub's 2026 security roadmap commoditizes *detection and enforcement* (SHA-pin policy, workflow lockfile, execution-protection rulesets, Data Stream). ActionsPlane's wedge rotates to **fleet-scale remediation** — the migration engine that gets an org compliant before enforcement day. See `plan.md` §14.

## Design principles

GitHub App (never PATs). Webhooks first, polling as a reconciliation safety net. **All edits go through PRs** — never a direct write to `main`. Workflow AST, not regex (`ruamel.yaml` round-trips comments/formatting). Event-sourced run history with materialised views for metrics. Self-hosted, no SaaS dependency.

## License

Apache-2.0 — see [`LICENSE`](LICENSE).
