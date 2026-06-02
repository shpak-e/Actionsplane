# ActionsPlane — Multi-Repo GitHub Actions Control Plane

> A self-hosted control plane for observing, auditing, and editing GitHub Actions across many repositories from a single UI/API.

---

## 1. Problem Statement

Teams that own many repos face four overlapping pains that no single OSS tool solves end-to-end today:

| Pain | What it looks like | Existing OSS coverage |
|---|---|---|
| **Status fragmentation** | Have to open N tabs to see if builds are green | Partial (`chriskinsman/github-action-dashboard`, `lf-workflow-dash`, `and-action`) — read-only, single org, no history |
| **Workflow drift** | Same workflow copy-pasted across repos slowly diverges; some pin SHAs, some pin `@main`, some lag versions | Partial (`Cerber Core` for contract enforcement, `git-xargs` for bulk edits) — no unified view |
| **Supply-chain blindness** | Per Adan Alvarez's 100-repo study, only 7% of popular security projects fully pin actions; ~50% have fully unpinned workflows. The `tj-actions/changed-files` retargeting attack hit anyone using `@v1` | StepSecurity (SaaS), `pin-github-action`, Dependabot — fragmented, per-repo |
| **No DORA / cost / flake view across repos** | Can't answer "which repo is burning the most minutes?" or "which workflow is flakiest org-wide?" | DataDog CI / Trunk / CI/CD Watch (SaaS, expensive), DevLake / Four Keys (heavy, deployment-focused) |

**Gap we hit:** an OSS, self-hosted control plane that combines **observe + audit + edit** with safe bulk operations via PRs.

---

## 2. Goals & Non-Goals

### Goals
- **Single pane of glass** for workflow runs across N repos/orgs, with history (not just current state).
- **Workflow drift detection** — surface divergence in shared workflows, pin states, runner choices, permission scopes.
- **Safe bulk editing** — apply a change to N repos' workflows, always via PRs, with diff preview and dry-run.
- **Supply-chain audit** — flag unpinned actions, untrusted publishers, overly permissive `GITHUB_TOKEN` scopes.
- **Cross-repo metrics** — minutes burned per repo/workflow, p50/p95 duration, flake rate, success rate, queue time.
- **Self-hosted, OSS, no SaaS dependency.** GitHub App, not PAT-based.

### Non-Goals (at least initially)
- Replacing GitHub Actions runner. We orchestrate, we don't execute.
- Full DORA platform (deployment frequency etc.). We expose data; DevLake/Four Keys can consume it.
- Supporting GitLab/Bitbucket in v1. Single-provider focus.
- Direct workflow execution from the UI (use `workflow_dispatch` — never bypass GH).

---

## 3. Differentiation vs. Existing Tools

| Tool | Read status | History/metrics | Drift detection | Bulk edit | Security audit | Self-hosted |
|---|---|---|---|---|---|---|
| `chriskinsman/github-action-dashboard` | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| `lf-workflow-dash` | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ (static) |
| `and-action` | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| `git-xargs` | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ (CLI) |
| `Cerber Core` | ❌ | ❌ | ✅ | ❌ | Partial | ✅ |
| DataDog CI / Trunk | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ SaaS |
| StepSecurity | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ SaaS (mostly) |
| **ActionsPlane** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

The bet: **no OSS tool combines the observe + audit + edit triangle.** That's the wedge.

---

## 4. Architecture (High-Level)

```
                ┌──────────────────────────────────────────────────┐
                │                  GitHub.com / GHES               │
                └─────▲──────────────▲─────────────────▲──────────┘
                      │webhooks      │REST/GraphQL     │workflow_dispatch
                      │              │                 │
        ┌─────────────┴──────┐  ┌────┴──────────┐  ┌──┴───────────────┐
        │ Webhook Ingestor   │  │ Sync Worker   │  │ Action Executor  │
        │ (FastAPI)          │  │ (Python, async)│ │ (PR creator)     │
        └─────────┬──────────┘  └─────┬─────────┘  └────┬─────────────┘
                  │                   │                 │
                  ▼                   ▼                 ▼
        ┌─────────────────────────────────────────────────────────────┐
        │              Postgres (runs, workflows, repos, audits)       │
        │                       + Redis (cache, queues)                │
        └─────────────────────────────────────────────────────────────┘
                  ▲                   ▲                 ▲
                  │                   │                 │
        ┌─────────┴──────────┐  ┌─────┴─────────┐  ┌───┴──────────────┐
        │ REST + GraphQL API │  │ Drift Engine  │  │ Audit Engine     │
        │ (FastAPI)          │  │ (AST diff)    │  │ (SHA-pin, perms) │
        └─────────┬──────────┘  └───────────────┘  └──────────────────┘
                  │
                  ▼
        ┌────────────────────┐
        │ Web UI (React)     │
        │ + CLI (Python)     │
        └────────────────────┘
```

### Key design choices
- **GitHub App, not PAT.** Per-install permissions, webhooks, no user-token sprawl. (`chriskinsman` validated this approach.)
- **Webhooks first, polling as fallback.** `workflow_run`, `workflow_job`, `push` (to `.github/workflows/**`), `installation_repositories`. Poll every 5 min as drift safety net.
- **All edits go through PRs.** Never `PUT /repos/{owner}/{repo}/contents/{path}` direct-to-main. Branch + PR + optional auto-merge after CI passes.
- **Workflow AST, not regex.** Parse YAML into a typed model (we need to reason about `jobs.*.steps[].uses`, permissions, concurrency, etc.). Use `ruamel.yaml` for round-trip preservation of comments and formatting.
- **Event-sourced run history.** Store every `workflow_run` and `workflow_job` event. Materialized views for fast metrics queries.

---

## 5. Feature Set

### 5.1 Observe (read-only)
- Cross-repo run dashboard: grid + list views, filter by repo / workflow / branch / status / time window.
- Per-run drill-down: jobs, steps, logs (linked to GitHub), timing breakdown.
- **Live status** via webhooks (sub-second UI updates via SSE/WebSocket).
- Run history with **trend lines** (success rate, p95 duration, queue time) per workflow.
- **Failure clustering** — group failures by error signature so 50 identical failures show as one row.
- "Top offenders" board: slowest workflows, flakiest workflows, most-failing branches.

### 5.2 Audit (analyze, don't change)
- **Pin audit** — every `uses:` reference classified as `sha-pinned | tag-pinned | branch-pinned | unpinned`. Sortable by repo, by action publisher, by risk.
- **Publisher trust audit** — flag actions from unverified publishers; allow per-org allowlist.
- **Permission audit** — workflows with no `permissions:` block (defaults to write-all on older repos), or with `contents: write` where it shouldn't be.
- **Secret usage audit** — which secrets are referenced where; flag secrets passed to third-party actions.
- **Runner audit** — `ubuntu-latest` vs pinned versions; self-hosted runner usage.
- **Concurrency audit** — workflows without `concurrency:` that race on deploys.
- **Deprecation scanner** — flag use of deprecated actions / Node 16 / etc.

### 5.3 Drift Detection
- Define a **workflow template** (the canonical version of `release.yml`, `ci.yml`, etc.).
- For each repo claiming to use the template, compute a structural diff against the canonical version (not textual — AST-level).
- Show drift severity: `identical | minor (whitespace) | content-drift | structural-drift`.
- One-click "open PRs to converge" → batch-creates PRs across drifting repos.

### 5.4 Edit (bulk operations)
All edits are **PR-based**, **dry-runnable**, and **scoped** by repo selector.

Operations:
- **Add / update / remove a workflow file** across N repos.
- **Pin all `uses:` to SHA** (resolves tags → SHAs via API, adds comment `# v5.0.0`).
- **Bump pinned SHAs to latest tag** (Renovate-style, in our scope).
- **Inject a step** into matching jobs (e.g., add `step-security/harden-runner` to all CI workflows).
- **Set `permissions:` block** to least-privilege defaults.
- **Convert duplicated workflows to a reusable workflow call** (advanced; v2).
- **Migrate workflows** to call a central reusable workflow.

Every operation:
1. Generates a diff per repo (preview UI).
2. Creates branch `actionsplane/<operation-id>`.
3. Opens PR with rationale, links to the originating "campaign."
4. Optionally enables auto-merge once required checks pass.
5. Reports per-repo status: `pr-open | merged | conflict | failed`.

### 5.5 Metrics & Cost
- **Minutes consumed** per repo/workflow/runner-type, billable estimate (we know runner pricing).
- **Queue time** (delta between `created_at` and `started_at`) — surfaces self-hosted runner saturation.
- **Flake rate** per workflow — failure-then-success-on-rerun-of-same-SHA.
- **Success rate** trends, with anomaly highlighting.
- **Cache hit rate** (if available via API).
- Per-team rollups via repo→team mapping.

### 5.6 Notifications
- Slack / webhook / email on: new failure, repeated failures (≥N consecutive), drift detected, audit regression (something was pinned, now isn't).
- "Quiet hours" and per-workflow severity rules.

### 5.7 CLI
For ops who live in the terminal (you):
- `actionsplane status` — recent runs across all watched repos
- `actionsplane audit pins --org foo` — list unpinned actions
- `actionsplane drift --template ci.yml` — show drift
- `actionsplane campaign create --op pin-shas --repos foo/*` — bulk operation
- `actionsplane campaign status <id>` — PR open/merged/conflict per repo

---

## 6. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI | You're a Python person; rich GitHub libraries; async-native |
| GitHub client | `PyGithub` + raw `httpx` for GraphQL | PyGithub covers REST; GraphQL needed for efficient cross-repo queries |
| YAML | `ruamel.yaml` | Round-trip preservation of comments & formatting in workflow files |
| Workflow model | Pydantic models for `Workflow / Job / Step` | Type-safe AST |
| DB | Postgres 16 | JSONB for raw payloads, relational for materialized views |
| Queue | Redis + RQ (or Arq for async) | Don't overbuild; no Kafka in v1 |
| Real-time | SSE (Server-Sent Events) | Simpler than WebSocket for one-way push |
| Frontend | React + Vite + TanStack Query | Standard, no exotic choices |
| CLI | `typer` + `rich` | Same Python ecosystem |
| Deploy | Docker Compose (dev), Helm chart (prod) | K8s-native fits your skills |
| Observability | OpenTelemetry → Prometheus + Grafana | Dogfood good practices |
| Auth | GitHub OAuth for users; GitHub App for repo access | No password mgmt |

---

## 7. Data Model (Sketch)

```sql
-- Identity
github_installations(id, account_login, account_type, installed_at)
repos(id, installation_id, owner, name, default_branch, watched, archived)

-- Workflows (the YAML files)
workflows(id, repo_id, path, name, state, last_modified_sha, parsed_ast jsonb)
workflow_versions(id, workflow_id, commit_sha, ast jsonb, ingested_at)

-- Runs (event-sourced)
workflow_runs(id, repo_id, workflow_id, run_number, head_branch, head_sha,
              event, status, conclusion, created_at, started_at, completed_at,
              actor, run_attempt, raw_payload jsonb)
workflow_jobs(id, run_id, name, status, conclusion, started_at, completed_at,
              runner_name, runner_group, labels, raw_payload jsonb)

-- Audit findings
audit_findings(id, repo_id, workflow_id, finding_type, severity, ref, message,
               first_seen_at, last_seen_at, resolved_at)
-- finding_type: unpinned_action, unverified_publisher, missing_permissions,
--               deprecated_action, dangerous_secret_flow, ...

-- Templates & drift
workflow_templates(id, name, canonical_ast jsonb, version)
template_bindings(repo_id, workflow_id, template_id, last_drift_check_at, drift_score)

-- Campaigns (bulk edits)
campaigns(id, name, operation, params jsonb, created_by, created_at, status)
campaign_targets(campaign_id, repo_id, status, pr_number, pr_url,
                 diff_preview, applied_at, error)
-- status: pending | dry-run-ok | pr-opened | pr-merged | pr-closed | conflict | failed

-- Metrics (materialized)
mv_workflow_daily(workflow_id, day, runs, successes, failures, p50_duration_s,
                  p95_duration_s, total_billable_minutes, flake_count)
```

---

## 8. Security Model

This tool has the keys to the kingdom. Treat it accordingly.

- **GitHub App with minimum permissions:**
  - `actions: read` (runs, jobs)
  - `contents: write` (only when bulk edits enabled; opt-in per install)
  - `pull_requests: write`
  - `metadata: read`
  - Subscribed events: `workflow_run`, `workflow_job`, `push`, `installation_repositories`
- **No PATs stored.** Ever.
- **All bulk ops require human approval in UI** — no `cron`-triggered bulk edits that touch main directly.
- **Audit log** of every write operation (who, what, when, which PRs).
- **Dry-run by default** for all edit operations; explicit "apply" step.
- **Webhook signature verification** (HMAC) on every inbound event.
- **Secrets at rest:** GitHub App private key in a KMS / sealed-secret; never in env files in prod.
- **Egress allowlist** for the executor — only `api.github.com` and configured webhook destinations.

---

## 9. Phased Roadmap

Four phases, each with a shippable artifact. Mirrors the GPUPlane structure.

### Phase 1 — Foundation & Observe (Weeks 1–3) — ✅ complete
**Goal:** Working dashboard showing runs across many repos with history.
- GitHub App scaffold + install flow
- Webhook ingestor (`workflow_run`, `workflow_job`)
- Postgres schema + initial migrations
- REST API: repos, workflows, runs, jobs
- React UI: repo list, run grid, run detail
- Basic metrics: success rate, duration p50/p95 per workflow
- Docker Compose dev stack
- **Demo:** install on a personal org, watch live runs stream in.

### Phase 2 — Audit (Weeks 4–5) — ✅ complete
**Goal:** Surface every security/hygiene problem in watched repos.
- Workflow AST parser (`ruamel.yaml` + Pydantic models)
- Pin audit (sha/tag/branch/unpinned classifier)
- Publisher trust audit + allowlist config
- Permission audit (missing `permissions:`, over-broad scopes)
- Deprecation scanner (curated list of deprecated actions)
- Audit findings persisted; UI "Security" tab per repo and org-wide
- CLI: `actionsplane audit pins`, `actionsplane audit perms`
- **[research] SARIF ingest** — import zizmor / octoscan / OpenSSF Scorecard findings into the unified findings model (the find→fix bridge; see §13).
- **[research] PR-time workflow linting as a GitHub Check** — annotate workflow PRs via the Checks API.
- **[research] Org supply-chain posture scorecard** — pin %, least-privilege, attestation, runner hygiene in one view.
- **[research] Workflow-log secret-leak scan** (post-run) and **runner-version EOL** added to the deprecation scanner.
- **Demo:** point at a popular OSS org, show audit report.

### Phase 3 — Drift & Templates (Weeks 6–7) — ✅ complete
**Goal:** Detect divergence from canonical workflows.
- Template registry (admin defines canonical workflows)
- Repo→template binding (manual + heuristic by filename)
- Structural AST diff engine
- Drift dashboard: which repos drift, on what, by how much
- Drift severity scoring
- **[research] Blast-radius / impact analysis** — reusable-workflow dependency graph: which repos/teams a change touches.
- **[research] Reusable-workflow catalog + adoption tracker** — internal inventory of who consumes which reusable workflow + version.
- **Demo:** define a CI template, show drift across 10 forks.

### Phase 4 — Edit (Weeks 8–10) — ✅ pin-shas operation + dry-run→PR campaign engine
**Goal:** Safe bulk operations via PRs.
- Campaign abstraction (operation + targets + status)
- Operations: `pin-shas`, `bump-pins`, `inject-step`, `set-permissions`, `replace-workflow`
- Dry-run + diff preview UI
- PR creation with rationale + auto-merge option
- Per-repo conflict handling and retry
- CLI: `actionsplane campaign create/status`
- **[research] SARIF finding → converge-PR** — turn an ingested finding directly into a fix campaign (closes the find→fix loop).
- **[research] Policy-as-code gate (Rego/CEL)** over workflows, versioned, dry-run → enforce modes.
- **[research] Least-privilege `permissions:` auto-PRs** computed from observed token usage; **immutable-action / GHCR awareness** on pin edits.
- **Demo:** pin every unpinned action across 20 repos in one campaign.

### Stretch (Post-v1)
- Cost dashboard with billable-minutes estimation
- Flake detection (rerun analysis)
- Slack/webhook notifications
- Self-hosted runner saturation view
- Reusable-workflow consolidation suggestions
- Multi-provider (GitLab) abstraction layer
- Compliance export (SLSA, SOC2-style evidence) — **[research]** map to CISA SSDF / EO 14028 form
- **[research] Cost-anomaly detection + per-team/per-workflow attribution + "cost of flake"** (GitHub Nov-2025 usage API)
- **[research] Attestation/provenance coverage report** (SLSA / artifact attestations, verified via cosign/slsa-verifier)
- **[research] Self-hosted runner / ARC fleet posture** (versions, ephemerality, scope, labels)
- **[research] Configure & report on GitHub org rulesets / required-workflows + Actions block/pin policy**

---

## 10. Open Questions / Risks

| Risk | Mitigation |
|---|---|
| GitHub API rate limits (5000/hr/install) | Heavy webhook reliance; GraphQL for batch reads; per-install budget tracker |
| AST diff is hard (YAML anchors, expressions, matrix expansion) | Start with a strict subset; reject "exotic" workflows from templating until v2 |
| Bulk PRs creating chaos in busy repos | Throttle per-repo PR rate; one open campaign PR per repo at a time |
| Webhook delivery loss | Reconcile via polling every 5 min, replay missed events |
| Scope creep into "another DataDog" | Strict non-goals; integrate with DevLake/Four Keys rather than rebuild DORA |
| GitHub changes APIs (it does) | Pin GH API versions; integration tests against `api.github.com`; alerting on schema drift |
| Self-hosted complexity scares users | Ship a one-command `docker compose up` demo with a fixture org |

---

## 11. Why This Is a Good Portfolio Project

For the Staff/Principal track specifically:
- **Platform engineering thesis** — productizing internal tooling that scales an org's DevOps practice. Exactly the work staff engineers do.
- **Multi-system orchestration** — webhooks, async workers, PR automation, AST manipulation, metrics aggregation. Touches everything.
- **Security/supply chain** — directly addresses CISA / SLSA / Sigstore-era concerns. Resonates with current hiring narratives.
- **Demonstrates judgment** — non-goals, phased delivery, explicit risk register. Reads as senior-IC thinking, not junior "I'll build everything."
- **Find→fix bridge (the headline wedge)** — ingest SARIF from best-in-class OSS linters (zizmor, octoscan, Scorecard) and convert findings into org-wide PR-based fixes. Every incumbent stops at "here's a finding"; ActionsPlane operationalizes the whole linter ecosystem instead of competing with it. See §13.
- **Composable with GPUPlane narrative** — both are "control plane" projects. Story bank: "I build platforms that make complex infrastructure manageable for the rest of the org."

---

## 12. Naming & Branding (TBD)

Working name: **ActionsPlane**. Alternatives: `ghctl`, `WorkflowOps`, `Pipeline Pane`, `ActionsHQ`.

Pick after Phase 1 demo when scope is concrete.

---

## 13. Research-Driven Feature Additions (2026-05)

Derived from competitive + platform research (full analysis: [`docs/feature-research.md`](docs/feature-research.md)). The market splits into find-only static linters (zizmor, octoscan), runtime EDR (StepSecurity), dependency pinners (Renovate/Dependabot), test-observability SaaS (Datadog), and coarse native enforcement (GitHub rulesets). **No OSS tool observes runs + audits posture + detects drift + ships bulk PR-based fixes — least of all by converting ingested security findings into converge-PRs.** That is the wedge these additions sharpen.

| # | Addition | Phase | Effort | Why it matters |
|---|---|---|---|---|
| 1 | **SARIF ingest → unified findings → converge-PRs** | 2 (ingest) + 4 (fix) | M | The find→fix bridge. Operationalizes zizmor/octoscan/Scorecard instead of competing. **Headline differentiator.** |
| 2 | PR-time workflow linting as a GitHub Check | 2 | S | Shift-left annotations on workflow PRs; native rulesets only block, don't explain. |
| 3 | Org supply-chain posture scorecard | 2 | M | One executive view: pin %, least-privilege, attestation, runner hygiene. |
| 4 | Workflow-log secret-leak scan (post-run) | 2 | M | StepSecurity charges for this; OSS version is a draw. Respect log-retention/PII. |
| 5 | Runner-version EOL in deprecation scanner | 2 | S | Self-hosted runners < v2.329.0 blocked Mar 2026 — timely. |
| 6 | Blast-radius / impact analysis | 3 | M | Reusable-workflow dependency graph: which repos/teams a change touches. |
| 7 | Reusable-workflow catalog + adoption tracker | 3 | M | Internal inventory; feeds the migrate-to-reusable edits. |
| 8 | Policy-as-code gate (Rego/CEL), dry-run → enforce | 4 | M | Actions-specific governance engine over OPA; strong Staff/Principal signal. |
| 9 | Least-privilege `permissions:` auto-PRs | 4 | M | Compute minimal scopes from observed token usage, not guesswork. |
| 10 | Immutable-action / GHCR awareness on pin edits | 4 | S | Post-`tj-actions` (CVE-2025-30066); aligns with GitHub's 2025 immutable actions. |
| 11 | Cost-anomaly detection + per-team attribution + "cost of flake" | Stretch | M | FinOps-for-CI on GitHub's Nov-2025 usage API; native is raw data only. |
| 12 | Attestation/provenance coverage report | Stretch | M | SLSA maturity dashboard; verify via cosign/slsa-verifier. |
| 13 | Compliance evidence export (CISA SSDF / EO 14028) | Stretch | M | Maps technical posture to recognized compliance form. |
| 14 | Self-hosted runner / ARC fleet posture | Stretch | M | ARC ships no security reporting; unowned in OSS. |
| 15 | Configure & report on GitHub rulesets / required-workflows | Stretch | M | Become the control surface for native enforcement; fix the long tail rulesets only block. |

> **Build status (2026-05):** GitLab CI provider (v2) — started: `.gitlab-ci.yml` parser + include/component pin audit behind a `Provider` protocol (`src/actionsplane/providers/`). See `docs/multi-ci-research.md` and `docs/memory.md`.

**Caveats carried from research:** CEL-over-workflows has no existing precedent (Rego/OPA is the proven path; treat CEL as an option). A "Cerberus/Cerber" Actions tool could not be verified and was excluded rather than invented.
