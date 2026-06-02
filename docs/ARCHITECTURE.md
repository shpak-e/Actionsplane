# ActionsPlane — Architecture

This document is the runtime/data-flow companion to [`../plan.md`](../plan.md). The plan covers *why* and *what*; this covers *how the pieces fit and talk to each other*. It is the document the `Projects/` registry convention requires for every active project.

## 1. System context

ActionsPlane sits beside GitHub (github.com or GHES) as a self-hosted service. It authenticates as a **GitHub App** — never a PAT — so it gets per-installation scoping, webhook delivery, and no user-token sprawl. Three boundaries cross into GitHub:

- **Inbound webhooks** — `workflow_run`, `workflow_job`, `push` (to `.github/workflows/**`), `installation_repositories`. This is the primary data source.
- **Outbound REST/GraphQL reads** — backfill on install, reconciliation polling, and the cross-repo batch queries the dashboard needs (GraphQL is far cheaper than N REST calls).
- **Outbound writes** — only `workflow_dispatch` and PR creation. ActionsPlane never executes workflows itself and never writes to a default branch directly.

## 2. Components

| Component | Module | Process | Responsibility |
|---|---|---|---|
| Webhook ingestor | `actionsplane.ingestor` | FastAPI (`:8001`) | Verify HMAC, fast-ack (<10s), enqueue raw event |
| Sync worker | `actionsplane.sync` | arq worker | Persist events, materialise views, run polling reconcile |
| API | `actionsplane.api` | FastAPI (`:8000`) | REST + GraphQL read model for UI/CLI |
| Audit engine | `actionsplane.audit` | in-process / worker | Pin, publisher, permission, deprecation, secret-flow audits |
| Drift engine | `actionsplane.drift` | in-process / worker | AST-level diff of workflows vs canonical templates |
| Executor | `actionsplane.executor` | worker | Campaigns: dry-run diffs, branch + PR creation, status tracking |
| GitHub client | `actionsplane.github` | library | App JWT → installation token; REST client |
| Event bus | `actionsplane.events` | library | Redis pub/sub publish + SSE subscribe |
| Metrics | `actionsplane.metrics` | library | Pure success/p50/p95/queue/flake computation |
| Web UI | `frontend/` | Vite + React | Repo list, run grid, run detail, live SSE updates |
| CLI | `actionsplane.cli` | `actionsplane` | Terminal access to status/audit/drift/campaigns |

Shared foundations: `actionsplane.config` (env-driven settings), `actionsplane.models` (typed workflow AST + enums), `actionsplane.db` (async SQLAlchemy + ORM).

## 3. Data flow

### Ingest path (the hot path)
```
GitHub ──webhook──▶ Ingestor ──verify HMAC──▶ enqueue(arq) ──▶ Redis
                                                                  │
                                          Sync worker ◀───────────┘
                                                │
                                                ▼
                            Postgres: workflow_runs / workflow_jobs (raw JSONB)
                                                │
                                                ▼
                              materialised views (mv_workflow_daily, …)
```
The ingestor stays deliberately thin so it always acks inside GitHub's delivery timeout. All real work happens in the worker. Lost deliveries are recovered by the polling reconcile loop (every `poll_interval_seconds`, default 300).

### Read path
```
React UI / CLI ──▶ API (REST + GraphQL) ──▶ Postgres (materialised views) + Redis (cache)
                          │
                          └── live updates ──▶ SSE stream (one-way push, simpler than WS)
```

### Edit path (campaigns)
```
User defines campaign (op + repo selector + params)
        │
        ▼
   dry_run() ── per-repo AST edit (ruamel.yaml round-trip) ──▶ diff preview (no writes)
        │  human approval in UI (required; no cron-triggered writes)
        ▼
   apply() ── branch actionsplane/<id> ──▶ PR + rationale ──▶ optional auto-merge on green
        │
        ▼
   per-target status: pending → dry-run-ok → pr-opened → pr-merged | conflict | failed
```

## 4. Why the workflow AST

The audit, drift, and edit engines all need to reason *structurally* about workflows — `jobs.*.steps[].uses`, `permissions`, `concurrency`, runner labels — not over raw text. So workflow YAML is parsed into the typed Pydantic models in `actionsplane.models.workflow` for **analysis**. For **edits**, the executor uses `ruamel.yaml` round-trip parsing so comments and formatting survive a programmatic change (a textual diff a reviewer can trust). Two representations, two jobs: Pydantic for reasoning, ruamel for rewriting.

## 5. Persistence model

Event-sourced: every `workflow_run`/`workflow_job` event is stored with its raw payload (JSONB), so history is never lossy and new metrics can be backfilled from raw data. Fast queries read from materialised views (`mv_workflow_daily`: runs, successes, failures, p50/p95 duration, billable minutes, flake count), refreshed by the worker. Postgres for everything relational + JSONB; Redis for the queue and read cache. See `actionsplane.db.models` and plan §7 for the full schema.

## 6. Security posture

The service holds the keys to the kingdom, so: GitHub App with least privilege (`actions:read`, `pull_requests:write`, `metadata:read`, and `contents:write` only when bulk edits are opt-in enabled); webhook HMAC verification on **every** inbound event (fail-closed if no secret configured); App private key referenced by path and mounted from KMS/sealed-secret, never inlined; dry-run by default with an explicit human apply step; an audit log of every write; and an egress allowlist (`api.github.com` + configured webhook destinations) on the executor. Full model in plan §8.

## 7. Deployment

Dev: `docker compose` brings up Postgres + Redis; the three Python processes (API, ingestor, worker) run on the host for fast reload. Prod: container images per process behind a Helm chart, observability via OpenTelemetry → Prometheus + Grafana (dogfooding good practice). Config is entirely env-driven (`ACTIONSPLANE_*`) so the same image runs in both.

## 8. Deferred decisions

Naming (working name "ActionsPlane") is decided after the Phase 1 demo. Multi-provider (GitLab/Bitbucket) is explicitly out of scope for v1 — the AST and client layers are kept provider-shaped so an abstraction can be introduced later without a rewrite. DORA/cost depth is left to DevLake/Four Keys, which consume ActionsPlane's data rather than competing with it.

## 9. Phase 1 implementation notes

The Foundation & Observe phase is implemented end-to-end. A few decisions worth recording:

**Hot path stays thin.** The ingestor only verifies the HMAC and enqueues; all persistence happens in the arq worker. This keeps the webhook ack well inside GitHub's ~10s delivery budget even under load, and means a slow database never causes dropped deliveries.

**Idempotent upserts.** Every run/job/installation write is a Postgres `INSERT ... ON CONFLICT DO UPDATE` keyed on the GitHub id. Webhook delivery is at-least-once, and the reconciliation poller re-ingests recent runs, so the same row is written repeatedly by design — the upsert makes that a no-op rather than a duplicate.

**Two recovery layers.** Webhooks are primary; the arq cron `reconcile()` (every 5 min) is the safety net. It mints an app JWT, exchanges per-installation tokens (cached per run), lists each watched repo's recent runs over REST, and upserts them. A missed webhook is invisible within one poll interval.

**Live updates without coupling.** After persisting, the worker publishes a slim envelope (id/status/conclusion only) to a Redis pub/sub channel. The API's SSE endpoint relays it to browsers, which invalidate their React Query caches. Pub/sub (not a queue) is deliberate: fan-out to N dashboards, no durability needed, because the REST read model — not the live tick — is the source of truth.

**Shared normalization.** Webhook `workflow_run` payloads and bare REST run objects flow through one `normalize_run_object()` builder, so the ingest path and the reconcile path can never drift into producing different rows.

**Metrics are pure.** Percentiles, success rate, queue time, and flake rate are computed by side-effect-free functions over plain records, so they unit-test without a database and run identically over live rows or materialised-view rows.

## 10. Phase 2/3 implementation notes

**Pure core, thin I/O.** The audit rule engine and the drift diff engine are side-effect-free functions over the typed `Workflow` AST. The only I/O is in the service layer (`audit/service.py`) that fetches files via the GitHub client and persists findings. This keeps the security logic exhaustively unit-testable and lets it fan out over an org cheaply.

**Finding lifecycle by fingerprint.** Each finding gets a stable `fingerprint = sha256(repo_id:path:type:ref)`. Re-audits upsert on the fingerprint — keeping `first_seen_at`, bumping `last_seen_at`, and reopening if previously resolved. After a repo audit, findings whose fingerprint wasn't seen this run are marked `resolved_at`. So the `audit_findings` table is a live, deduplicated ledger with full history, not an append-only dump.

**Drift is structural, not textual.** The diff engine compares parsed ASTs, so reordered keys and comments don't register, but a changed action version (content drift) or an added/removed job or step (structural drift) does. A worst-wins severity ladder (`identical < minor < content < structural`) gives a single sortable score per binding — the input to the "open converge-PRs" action in Phase 4.

**Why a fingerprint instead of a composite unique key.** `ref` and `workflow_id` are nullable, and Postgres treats NULLs as distinct in unique constraints — which would defeat dedup for null-ref findings (e.g. "missing permissions"). Hashing the coalesced tuple into one non-null column sidesteps that entirely.

## 11. Phase 4 implementation notes (edit, in progress)

**Round-trip rewriting.** Bulk edits use ruamel's round-trip parser, not the analysis parser, so comments, key order, quoting, and indentation survive — the resulting PR diff touches only the lines that actually changed, which a human can trust. The audit/drift engines read the typed Pydantic AST; the editor mutates the ruamel document. Two representations, two jobs.

**Resolver injection keeps edits pure.** `pin_workflow_to_sha(text, resolver)` takes the tag→SHA lookup as a callable, so the rewrite is a pure function unit-tested offline; only the campaign service binds the real GitHub-backed resolver. The operation registry (`OPERATIONS`) makes adding `bump-pins`, `set-permissions`, `inject-step` a matter of adding one function.

**Existing comments are preserved.** When annotating a pinned ref with its tag (`@<sha>  # v4`), any pre-existing end-of-line comment is kept and the tag appended, so pinning never silently destroys author intent.

## 12. Phase 4 — campaign engine (complete)

**Dry-run → apply, always via PRs.** `run_dry_run` computes per-repo edits and stores the unified diff on each `campaign_target` with **no writes**; `apply_campaign` (gated by `bulk_edits_enabled`, human-triggered) opens one PR per repo: branch `actionsplane/<op>-<id>` → commit each changed workflow → open PR with a rationale body. Per-target status (`pending → dry-run-ok → pr-opened | conflict | failed`) means one repo's conflict never sinks the campaign.

**Pre-resolved, pure rewrite.** Pinning needs a tag→SHA lookup against each *action's* repo, which is async I/O. To keep the rewrite a pure function, the service resolves every ref first (`get_commit_sha` per distinct ref, cached) into a dict, then calls `pin_workflow_to_sha(text, dict.get)`. I/O and transformation stay cleanly separated and independently testable.

**Apply recomputes.** `apply_campaign` re-runs the dry-run against current `main` immediately before opening the PR, so a stale preview can't push an outdated edit — the diff a reviewer sees is computed from HEAD at apply time.
