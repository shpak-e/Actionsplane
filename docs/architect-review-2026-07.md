# ActionsPlane — External Architect Review (2026-07-03)

Independent review: principal-SRE/architect lens on the codebase, fresh competitive research (July 2026), relevance assessment, and a proposed next roadmap. Complements (does not repeat) `docs/staff-review.md`, `docs/review-findings-2.md`, `docs/memory.md`.

---

## 1. Engineering overview

**Scale:** ~5,600 LOC Python across `src/actionsplane` (config, models, ingestor, sync, executor, api, drift, audit, metrics, events, github, providers, observability, cli), ~1,700 LOC React/TS frontend, 31 test files (~2,500 LOC, 129 tests), 9 Alembic migrations, docker-compose + kustomize + Helm.

**What's done well (would call out in any staff-level review):**

- **Clean pure-core/IO-shell layering.** The audit rule engine, AST diff, metrics, and the pin-shas edit are pure functions given resolved inputs; IO lives at the edges (ingestor, worker, GitHub client). This is what makes 129 hermetic tests possible and it's the single strongest structural property of the codebase.
- **Correctness under distributed-systems reality.** Out-of-order webhook guard (late `in_progress` can't regress a `completed` run), `X-GitHub-Delivery` dedup, idempotent upserts, expiry-aware race-free token cache with per-installation locks, ETag/304 + `Retry-After` backoff, SSE disconnect-safe bus teardown. Most side projects never get here; most production systems get here only after incidents.
- **A real safety model for writes.** Dry-run→apply with SHA re-use (TOCTOU-safe: a tag retargeted between preview and apply can't change what lands), fail-closed on missing auth, PR-only writes, feature-gated (`bulk_edits_enabled`), retry-safe branches.
- **Ops maturity signals:** OTel trace propagated across the arq queue boundary (ingest→worker→audit→SARIF as one trace), property-based tests (hypothesis) pinning YAML round-trip invariants, dogfooded SHA-pinned CI, Helm chart with non-root/read-only-FS/probes.

**Grades (my calibration, agrees with the sub-review):** code quality ~8/10, security ~7.5/10, scalability ~6.5/10.

**Gaps an outside principal would flag** (✱ = new, not already in your tracked backlog):

| # | Finding | Severity |
|---|---|---|
| 1 | **No live validation.** The entire write path is proven only against a mock GitHub. This is the biggest unknown and you know it — everything else is secondary until the ingest→audit→SARIF→campaign loop runs against a real org. | Blocker for any claim beyond "preview" |
| 2 | ✱ **No multi-tenancy/RBAC/audit-log.** Single bearer token = one role. plan.md §8 promises an audit log of writes; it isn't there. For a tool holding `contents:write` over a fleet, this is the first question a security reviewer asks. | High |
| 3 | ✱ **HA story is untested.** Reconcile cron + sweeps: what happens with 2 worker replicas? If org sweeps aren't lease/lock-guarded, scaling the worker double-audits and double-PRs. Helm lets you set `replicas: 2`; the code's behavior under it is undefined. | High |
| 4 | Rate-limit sustainability: per-install budget tracker from plan.md §10 not implemented; a 500-repo org sweep can exhaust 5k/hr. Bounded concurrency ≠ budget. | Med-High |
| 5 | Campaign idempotency on partial failure (re-apply after mid-campaign crash) is under-tested. | Med |
| 6 | ✱ **No load/soak testing.** SSE fan-out, webhook burst (org-wide push), Postgres growth (raw payload JSONB, no retention/partitioning policy). `workflow_runs.raw_payload` will dominate storage within months on a busy org. | Med |
| 7 | GitLab provider is a parser + audit seam, not a provider — no ingest, no write path. Fine, but label it "spike" not "v2 started" to keep the README honest. | Low-Med |
| 8 | ✱ Frontend has no test coverage at all; acceptable at this stage, but the Pipelines graph logic (layered layout, edge inference) is complex enough to deserve unit tests. | Low |
| 9 | ✱ No GHES support statement. Self-hosted buyers overlap heavily with GHES shops; even "untested but URL-configurable" is worth stating. | Low |
| 10 | Materialized-view metrics strategy from plan.md §7 (`mv_workflow_daily`) not visible in migrations — metrics are computed, fine now, but the event-sourced history has no rollup path. | Low |

---

## 2. Market check (fresh research, July 2026)

### The big event: GitHub is building parts of your product natively

GitHub's **Actions 2026 security roadmap** (published 2026-03-26) is the most important development since plan.md was written:

| GitHub native (shipped / coming) | Timeline | Overlaps ActionsPlane... |
|---|---|---|
| **SHA-pinning enforcement** in allowed-actions policy | Shipped Aug 2025 | Pin *enforcement* (not remediation) |
| **Workflow lockfile** — `dependencies:` block pinning direct + transitive actions by SHA (go.mod/go.sum model) | Public preview 3–6 mo, GA ~6 mo | Pin audit + immutable-action awareness (§13 #10) |
| **Workflow execution protections** — centralized rulesets (actor rules, event rules, evaluate mode) | Preview 3–6 mo | Policy-as-code gate (§13 #8) |
| **Scoped secrets** + secrets-permission separation | Preview 3–6 mo | Secret-usage audit (partially) |
| **Actions Data Stream** — near-real-time execution telemetry to S3/Event Hub | Preview 3–6 mo, GA 6–9 mo | Observe pillar (partially) |
| **Native egress firewall** for hosted runners (L7, monitor→enforce) | Preview 6–9 mo | — (this hits StepSecurity's harden-runner, not you) |
| Repository dashboard GA, Feb 2026 Actions updates | Shipped | Single-repo observe only |

### Rest of the field (unchanged in shape since your 2026-05 research)

Find-only scanners matured — **zizmor** (24+ audit rules, now the de-facto standard, and the subject of academic comparisons), octoscan, poutine, actionlint; new pin-autofixers (**pinny**, **frizbee**) join `pin-github-action`; Renovate/Dependabot still per-repo PRs, no campaign/fleet semantics. **StepSecurity** remains the SaaS runtime play — now directly threatened by GitHub's native egress firewall. Dashboards (gitactionboard, chriskinsman, actions-dashboard) still read-only/no-history. Metrics: **Apache DevLake** (DORA via Grafana, heavy), **CDviz** (CDEvents-based, real-time push, presented at FOSDEM 2026) — both observe-only, neither audits nor edits.

**Conclusion: the observe + audit + edit-via-PR triangle is still unoccupied in OSS.** Nobody converts findings into fleet-wide converge-PRs. Your §13 wedge survives.

### But the wedge must rotate

GitHub is systematically closing the **detection and enforcement** layers (policies block, lockfiles verify, rulesets gate, data stream observes). What GitHub structurally will not do is **remediation at fleet scale**: enforcement that *breaks* every non-compliant repo creates the migration problem ActionsPlane solves. When an org flips "require SHA pinning," hundreds of workflows stop running until someone opens hundreds of pin PRs. That someone is a campaign engine.

**Reposition:** from "self-hosted audit + observe control plane" → **"the fleet remediation & migration engine for GitHub's new policy era."** Detection commoditized (zizmor + native policy); remediation didn't. Same code, sharper story — and a much better staff/principal narrative: *"GitHub ships enforcement; I ship the tool that gets a 500-repo org compliant before enforcement day."*

### Relevance verdict

**Still relevant — arguably more than in 2025** (tj-actions, Nx, trivy-action attacks keep CI/CD supply chain on every 2026 security roadmap), **but on a clock.** The audit-only value decays as native policy + zizmor commoditize detection. The edit/campaign/drift value appreciates, because every new native enforcement knob creates a new fleet-migration problem. Build toward the knobs.

---

## 3. Recommended next roadmap

### Now (unblock credibility)
1. **Live validation** against a real org — everything else is theater until this lands. Produces the write-path cassette corpus too.
2. **Write-op audit log** (append-only table: who/what/when/PR URLs) + minimal RBAC (read vs. operate tokens). Cheap, closes the plan.md §8 promise.
3. **Worker lease/lock for sweeps** (Postgres advisory lock or Redis lease) so `replicas > 1` is safe. Document the HA posture either way.
4. Payload retention policy (prune/partition `raw_payload`) before the DB grows into it.

### Next (rotate the wedge — new features, not in your §13 list)
5. **Policy-readiness simulator.** Evaluate GitHub's SHA-pinning policy / execution protections against the stored fleet AST: "flipping this org policy today breaks these 47 workflows in 23 repos" → one click → fix campaign. GitHub's evaluate mode shows *what* would block; you show *what to change* and then change it. First-mover, perfect fit for existing audit+campaign machinery, and the single best demo this project could have.
6. **Lockfile campaigns.** When the `dependencies:` lockfile hits preview (~Q4 2026): generate, verify, and refresh lockfiles fleet-wide via campaigns. Nothing else will exist for this on day one; Renovate will take months to catch up on transitive resolution.
7. **SARIF *ingest* → converge-PR** (your §13 #1 — emit side is done; ingest is the half that makes the headline true). Operationalize zizmor rather than compete with it.
8. **Actions Data Stream ingest adapter** (S3 batch reader) as a third population path beside webhooks/offline — positions the Observe pillar as a consumer of GitHub's richest telemetry instead of a competitor to it.

### Later
9. Scoped-secrets migration campaigns (same pattern: native knob → fleet migration).
10. Renovate/Dependabot coexistence detection (skip `bump-pins` where Renovate owns the repo — avoids the "why two bots" objection).
11. GHES config support (self-hosted buyers ∩ GHES shops is large).
12. LLM-assisted remediation for non-mechanical findings (script-injection rewrites) — keep human-approved PR gate.

### Deprioritize (research says: losing bets)
- **Workflow-log secret-leak scan** (§13 #4) — GitHub push protection + Data Stream squeeze this.
- **Runner/ARC fleet posture** (§13 #14) — GitHub's "runners as protected endpoints" direction owns it.
- Generic Rego/CEL policy engine (§13 #8) — narrow it to the readiness-simulator framing above; don't compete with native rulesets on enforcement.
- Deep GitLab provider before GitHub live validation — breadth before depth is the classic portfolio-project failure mode.

---

## 4. Sources

- [GitHub Actions 2026 security roadmap](https://github.blog/news-insights/product-news/whats-coming-to-our-github-actions-2026-security-roadmap/) (lockfile, execution protections, scoped secrets, Data Stream, egress firewall)
- [GitHub changelog: Actions policy supports blocking + SHA pinning](https://github.blog/changelog/2025-08-15-github-actions-policy-now-supports-blocking-and-sha-pinning-actions/) (Aug 2025)
- [Repository dashboard GA](https://github.blog/changelog/2026-02-24-repository-dashboard-is-now-generally-available/) · [Actions Feb 2026 updates](https://github.blog/changelog/2026-02-05-github-actions-early-february-2026-updates/)
- Scanner landscape: [comparison study (arXiv)](https://arxiv.org/html/2601.14455v2) · [DaTosh scanner comparison](https://datosh.github.io/post/github_action_scanner/) · [octoscan](https://github.com/synacktiv/octoscan) · [awesome-github-actions-security](https://github.com/johnbillion/awesome-github-actions-security)
- Observability: [CDviz alternatives analysis](https://cdviz.dev/docs/alternatives/) · [FOSDEM 2026 CDviz talk](https://fosdem.org/2026/schedule/event/ATTMUV-building_cdviz_lessons_from_creating_cicd_observability_tooling/) · [Grafana CI/CD observability](https://grafana.com/blog/ci-cd-observability-via-opentelemetry-at-grafana-labs/)
- Dashboards: [gitactionboard](https://github.com/otto-de/gitactionboard) · [chriskinsman/github-action-dashboard](https://github.com/chriskinsman/github-action-dashboard)
- [Wiz: Hardening GitHub Actions](https://www.wiz.io/blog/github-actions-security-guide) · [StepSecurity](https://www.stepsecurity.io/github-actions-and-stepsecurity)
