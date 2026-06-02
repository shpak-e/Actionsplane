# ActionsPlane — Directions Research (2026-05)

> Research only (no code). Horizon: 2025–2026 sources. Builds on, and does not repeat,
> `docs/feature-research.md` (competitive backlog), `docs/multi-ci-research.md` (GitLab port),
> and `plan.md §13` (research-driven additions). Where an existing item's *status* has changed,
> that change is called out explicitly; otherwise prior items are not re-litigated.
>
> **Headline shift since the prior docs:** the find→fix gap ActionsPlane was built around has
> *narrowed at both ends* — zizmor now ships **auto-fixes**, and on 2026-03-26 GitHub published a
> **2026 Actions security roadmap** that moves much of the audit/pin/observability surface *into the
> platform itself* (workflow dependency lock files, policy-driven execution, scoped secrets, an
> Actions Data Stream, and a native egress firewall). This reframes ActionsPlane: the defensible
> center is no longer "find→fix" alone — it is **org-wide orchestration, migration, and evidence
> across the long tail and across providers, sitting on top of the new native primitives.**

---

## (a) What's changed in the landscape since the prior docs

### 1. zizmor closed part of the find→fix gap (the prior headline differentiator)
The prior docs leaned heavily on "zizmor finds; it does not fix." That is now only half true.
zizmor shipped **auto-fix** support across recent releases: `template-injection`, then `artipacked`
(v1.10.0), `bot-conditions` (v1.11.0), `cache-poisoning` / `known-vulnerable-actions` /
`insecure-commands` (v1.12.0), and `obfuscation` (v1.13.0). Trail of Bits also published a 2026
hardening writeup of the analyzer. Per a 2026 arXiv comparison of Actions scanners, zizmor covers
the broadest weakness set (~10 weakness classes, ~23 rules).

**Implication, not a retreat:** zizmor's autofix is **single-repo, local, file-in-place**. It does
not open PRs, has no org rollout, no campaign/dry-run state, no observe/drift, no posture rollup, no
review-gated GitOps model. ActionsPlane's wedge survives but must be **re-pointed**: position not as
"the thing that fixes what zizmor finds" but as **"the org-scale campaign + evidence plane that
*orchestrates* zizmor's own autofixes (and other linters) across N repos as reviewable PRs."** Treat
zizmor as a fix *engine* to drive, not just a SARIF source. This is a sharpening of `feature-research`
item #1 / `plan §13` #1, with a materially different framing.

### 2. GitHub's 2026 Actions security roadmap (the big one)
Published 2026-03-26 (updated 03-30). GitHub is moving the platform "secure-by-default" across three
layers, with most items at 3–9 month preview→GA targets:

- **Workflow dependency lock files** — a new `dependencies:` section in workflow YAML that pins all
  *direct and transitive* action dependencies to commit SHAs, "like Go's `go.mod`+`go.sum`," with
  fail-fast hash verification and `gh` CLI resolution. **This directly overlaps ActionsPlane's
  pin-to-SHA campaign** — and goes further (transitive). Status change: pin-to-SHA bulk PRs move from
  "table-stakes differentiator" toward **"adopt-and-orchestrate the native primitive."**
- **Policy-driven execution** (ruleset-based actor/event rules) **with an "evaluate mode"** dry-run.
  This overlaps `plan §13 #8` (policy-as-code) and #15 (configure rulesets). GitHub's native policy is
  coarse (who/which-event) and has no remediation; the *evaluate-mode* concept validates
  ActionsPlane's dry-run→enforce design but also means ActionsPlane should **report on and converge
  toward** native rulesets rather than reinvent the gate.
- **Scoped secrets** + reusable-workflow inheritance changes + separating secret-management from write
  access. New *audit surface*: "which secrets are broadly inherited / over-scoped" becomes a
  first-class finding type that did not exist when the prior docs were written.
- **Actions Data Stream** — near-real-time execution telemetry to S3 / Azure Event Hub, at-least-once,
  common schema. This is a **richer ingest source than webhooks** (dependency-resolution + action-usage
  + future network events) and partially overlaps ActionsPlane's Observe pillar. Opportunity: *ingest
  the Data Stream* instead of/in addition to webhooks where available.
- **Native egress firewall** (L7, runner-external, monitor→enforce) — this is GitHub building
  StepSecurity-harden-runner-style control natively. Confirms the runtime-control direction but is
  GitHub-hosted-only; self-hosted/ARC fleets remain ActionsPlane's territory.
- Adjacent changelogs: **immutable OIDC subject claims** (new repos after 2026-06-18 get
  `owner-id/repo-id` in `sub`), repo **custom-property OIDC claims GA** (2026-04-02), read-only
  `GITHUB_TOKEN` default for new repos. All are new *auditable posture facts*.

**Net:** GitHub is commoditizing the *primitives* (pinning, coarse policy, telemetry, egress). It is
**not** building cross-org orchestration, PR-based bulk migration, drift-to-canonical-template,
multi-provider coverage, compliance evidence export, or "adopt the new primitive across 400 repos for
me." That migration/orchestration/evidence layer is where ActionsPlane should now plant its flag.

### 3. New incidents keep the threat live
A **May 2026 tag-redirect attack** (popular Action tags repointed to imposter commits to steal CI/CD
creds) plus the roadmap's own citing of **Nx** and **trivy-action** compromises alongside tj-actions
show the attack class is recurring, not a one-off. The pin/posture story stays urgent; the *new* angle
is **migration to immutable releases + dependency lock files** as the durable fix, not just SHA pinning.

### 4. CI FinOps got a hard forcing function
GitHub announced **pricing changes**: hosted-runner prices down up to ~39% on 2026-01-01, **and a new
$0.002/min "Actions platform charge" applied to *self-hosted* runner usage from 2026-03-01** (public
repos free). This turns the prior "cost-anomaly detection (Stretch)" item from a nice-to-have into a
**timely, board-visible** problem: self-hosted minutes now cost money, so per-team/per-workflow
attribution and waste-finding have real ROI. Status change: promote FinOps from Stretch.

### 5. AI-agent CI risk is a new, underserved category
2026 saw real incidents: a single malicious PR title hijacking Claude Code Review, Gemini CLI Action,
and Copilot Coding Agent simultaneously to exfiltrate secrets; CVE-2025-53773 (hidden prompt injection
→ RCE via Copilot, CVSS 9.6). "AI agent sprawl" / autonomous PR bots writing workflows is now a named
governance problem. None of the prior docs cover **auditing AI-authored / agent-triggered workflow
changes** — a genuinely new lane.

---

## (b) Prioritized NEW directions

New since the prior docs unless marked. Effort S/M/L. "Signal" = Staff/Principal portfolio value.
Sorted roughly by value-per-effort × timeliness.

| Idea | Category | Value | Effort | Why now / portfolio signal |
|---|---|---|---|---|
| **Orchestrate zizmor's *autofixes* (not just SARIF) into org-wide review-gated PR campaigns** | security/edit | High | S–M | zizmor now autofixes locally but has no fleet rollout; ActionsPlane becomes the campaign+evidence plane that drives it across N repos. Re-points the headline wedge to survive zizmor's autofix. |
| **`dependencies:` lock-file adoption campaign + drift/coverage report** | edit/security | High | M | GitHub's 2026 lock files are the durable fix for tj-actions-class attacks but "hard at scale." Be the tool that *rolls them out org-wide as PRs* and reports coverage. Rides the platform wave instead of fighting it. |
| **MCP server exposing ActionsPlane to AI agents (read-first; gated write)** | platform/AI | High | M | Lets Claude/Copilot/Cursor query posture, drift, findings, and *propose* (never auto-merge) converge-PRs. 13k+ MCP servers shipped in 2025; a governance-plane MCP with read-default + scoped write is a strong, current Staff signal and a natural fit for the all-edits-via-PR thesis. |
| **Audit AI-authored / agent-triggered workflow changes** (provenance of who/what wrote a workflow; flag agent PRs touching `.github/workflows`, `pull_request_target`, secret access) | security/audit | High | M | Brand-new threat class (2026 multi-agent prompt-injection, CVE-2025-53773). Nobody governs *AI-generated CI sprawl* yet. Distinctive narrative. |
| **Over-scoped / broadly-inherited secret audit** (reusable-workflow secret inheritance; map toward GitHub scoped-secrets) | audit | High | M | New surface created by the 2026 scoped-secrets roadmap; pairs with existing least-privilege work. Underserved today. |
| **Ingest the Actions Data Stream as a richer Observe source** (S3/Event Hub schema) alongside webhooks | observe | Med-High | M | Replaces/augments webhook ingest with dependency-resolution + action-usage telemetry GitHub now emits. Demonstrates ingest-abstraction maturity. |
| **CI FinOps: self-hosted-minute attribution + waste/"cost-of-flake" + the new $0.002/min platform charge** (promoted from Stretch) | metrics | Med-High | M | The Mar-2026 self-hosted charge + Jan-2026 repricing make this board-visible; native billing is raw data only. |
| **"Autofix bot" mode: scheduled, policy-driven converge-PR bot** (Renovate-shaped cadence, but for *posture* not deps) | edit/platform | Med-High | M | Turns one-shot campaigns into a continuously-converging governance bot; the operational maturity step beyond manual campaigns. High signal. |
| **Egress-allowlist generator** — synthesize harden-runner / native-egress-firewall allowlists from observed network/usage telemetry | security | Med-High | M | GitHub's native firewall + StepSecurity both need *allowlists*; generating them from observed traffic (monitor→enforce) is the unowned glue. Self-hosted/ARC stays ActionsPlane's turf. |
| **Provenance/SLSA + SBOM-for-CI coverage with VEX** (which repos emit provenance; verify; export) — extends `§13 #12` | security | Med | M | SLSA L2 is "weeks" now but adoption is patchy and self-hosted-runner-clunky; a *fleet coverage* view + VEX correlation is still unowned. Status: still open, more mature tooling to lean on. |
| **Compliance evidence export** (CISA SSDF / EO 14028 / CISA 2025 SBOM minimum elements) — `§13 #13`, sharpened | security | Med | M | Now maps to *new* posture facts (lock-file coverage, scoped-secrets, immutable OIDC). Enterprise-credible. |
| **Report on & converge toward native rulesets / policy-execution evaluate-mode** — `§13 #15`, status-updated | platform | Med | M | GitHub shipped evaluate-mode; ActionsPlane should consume policy-insights and fix the long tail, not reimplement the gate. |
| **Immutable-action / immutable-release migration tracker** (mutable-tag → immutable release adoption) | edit/security | Med | S–M | Publishing-side half of the 2026 roadmap; pairs with lock-file campaigns. |

**Demoted / status-changed from prior docs:**
- *Pin-to-SHA bulk PRs* (`§13 #10`, `feature-research #3`): still core but now **converging toward
  GitHub's native `dependencies:` lock files** — reframe as "adopt the native primitive at scale," not
  "we invented pinning."
- *Policy-as-code gate (Rego/CEL)* (`§13 #8`): GitHub's ruleset-based policy-execution (with
  evaluate-mode) lands in the same space. ActionsPlane's edge is **remediation + long-tail coverage +
  cross-provider**, not the gate itself. Keep, but narrow the pitch.
- *Workflow-log secret-leak scan* (`§13 #4`): GitHub's egress firewall + Data Stream reduce the runtime
  exfil surface natively; keep as a static/log-time complement, lower priority.

---

## (c) Engineering best-practices checklist (mapped to this repo)

Verified against the current tree (`src/actionsplane/...`). ✅ = present, ⚠️ = partial/gap, ❌ = absent.

### Webhook ingest / async correctness
- ⚠️ **Delivery-ID dedup.** `ingestor/app.py` does verify→enqueue→ACK correctly (good, matches 2025
  best practice), and `db/repository.py` is idempotent via Postgres `ON CONFLICT` upserts. **But there
  is no explicit `X-GitHub-Delivery` dedup table.** Upsert idempotency protects the *row* but not
  side-effects (re-enqueue, re-audit, re-notify) on at-least-once redelivery. **Add a
  `processed_deliveries(delivery_id, received_at)` check** in `ingestor/app.py` before enqueue (the
  delivery header is currently not even read). Low effort, removes a whole class of double-processing.
- ⚠️ **Dead-letter + retry policy.** arq is in use (`sync/queue.py`, `sync/worker.py`) but no visible
  DLQ / max-retry / poison-message handling. Add bounded retries + a dead-letter store; surface stuck
  deliveries.
- ✅ **Fail-closed signature verification** (`ingestor/app.py` returns 503 if no secret; 401 on bad
  HMAC). Good.
- ⚠️ **`completed_at` heuristic** in `ingestor/events.py` uses `updated_at` as a proxy when
  `conclusion` is set — fine, but document it as an approximation and reconcile against the REST run
  object in the poller (which already shares `normalize_run_object` — good reuse).

### PR-writing / campaign idempotency
- ⚠️ **PR idempotency by branch name only.** `executor/service.py` uses `actionsplane/{operation_id}`
  as the branch. If a campaign re-runs, branch-exists handling and "is there already an open PR for
  this (repo, operation)?" need to be explicit and tested. Add an **idempotency key per (campaign,
  repo, operation)** persisted on `campaign_targets`, and make `create_branch`/`create_pull_request`
  safe under retry (find-or-create, not create-or-500).
- ⚠️ **No detection of human edits on an open converge-PR** before force-updating — important for the
  "all edits via review" trust thesis. Decide and document the conflict policy.

### Testing strategy (highest-leverage gap)
- ❌ **No contract tests against GitHub / no VCR-style cassettes / no property-based tests.**
  `pyproject.toml` dev deps are `pytest`, `pytest-asyncio`, `pytest-httpx`, `ruff` only. For a
  webhook+async+PR-writing system this is the biggest maturity gap and the clearest Staff signal:
  - **Recorded HTTP cassettes** (e.g. `vcrpy`/`pytest-recording`, or build on `pytest-httpx`) for the
    `github/client.py` REST/GraphQL calls so write paths are tested without a live token. Today
    `tests/test_github_client.py` exists but there's no cassette corpus.
  - **Property-based tests (`hypothesis`)** for the two correctness-critical pure cores: the
    **pin classifier / `audit/pins.py`** (round-trip: classify→edit→re-classify is stable;
    SHA/tag/branch invariants) and the **ruamel round-trip edit in `executor/operations.py`**
    (property: editing only `uses:` values preserves comments, key order, and re-parses to the same AST
    modulo the intended change). This is exactly where subtle YAML round-trip bugs hide.
  - **Webhook fixture corpus** for `ingestor/events.py` normalizers (real `workflow_run`/`workflow_job`/
    `installation` payloads) — partly there, expand and assert dedup.

### Observability
- ❌ **OTel declared but not wired.** `pyproject.toml` includes `opentelemetry-sdk`,
  `-exporter-otlp`, `-instrumentation-fastapi`, but `grep` finds **zero** `opentelemetry`/`trace`/`span`
  usage in `src/`. Wire spans across the three signature paths: **ingest** (`ingestor`→`sync/worker`),
  **audit** (`audit/service.py`→`engine`), and **campaign** (`executor/campaigns.py`→`service`). Trace
  context should follow the arq job so a webhook delivery → audit → PR is one trace. High signal, deps
  already present, low incremental cost.

### Data store scaling / retention
- ⚠️ **No partitioning or retention on the event tables.** `db/models.py` has `workflow_runs` /
  `workflow_jobs` as plain tables with `created_at` but no PARTITION and no retention job. As an event
  store this will grow unbounded. Add **time-range partitioning** (monthly) on `workflow_runs`/
  `workflow_jobs` + a retention/rollup policy (keep aggregates in `metrics/`, drop raw beyond N days).
  Document the event-sourcing retention contract.
- ✅ **Finding fingerprinting** (`db/models.py` `fingerprint` unique col; `repository.py` reopen-on-
  conflict) is a clean idempotent-finding design. Keep.

### Multi-tenancy / isolation / secrets
- ⚠️ **Tenant isolation.** Installations are modeled (`github_installations`), but verify every query
  is **scoped by installation/org** (no cross-tenant leakage) and consider Postgres RLS or a mandatory
  `installation_id` filter at the repository layer. The 2026 MCP incident class (Asana tenant-isolation
  flaw) makes this a live concern if an MCP/API surface is added.
- ⚠️ **Secrets handling.** GitHub App private key + webhook secret live in settings (`config.py`); for
  a credible self-hosted product, document a secrets-manager path (env/file/Vault) and key rotation.

### API design
- ⚠️ **Pagination + idempotency keys on writes.** Confirm list endpoints paginate (cursor preferred)
  and that campaign-trigger POSTs accept a client **`Idempotency-Key`** (ties into the PR-idempotency
  point above). `api/schemas.py` / `api/app.py` are the touchpoints.

### Supply-chain hygiene for ActionsPlane's OWN releases (dogfood)
- ⚠️ **Dogfood the product on itself.** `.github/workflows/ci.yml` exists; the credibility move is to
  (a) **SHA-pin + adopt the new `dependencies:` lock file** in ActionsPlane's own CI, (b) emit **SLSA
  build provenance / artifact attestations** on releases, (c) **sign releases** (cosign/Sigstore), and
  (d) run **zizmor + Scorecard** on itself in CI and publish the badge. "We pass our own audit and ship
  signed, attested, lock-filed releases" is the strongest possible portfolio proof.

---

## (d) Three "big bet" directions

**1. ActionsPlane as the org-scale *orchestration + evidence* plane on top of GitHub's 2026 primitives
(not a competitor to them).** GitHub is natively shipping pinning (lock files), coarse policy
(evaluate-mode), telemetry (Data Stream), and egress control. The losing move is to reinvent those; the
winning move is to be the **fleet-wide adopter and reporter**: roll out `dependencies:` lock files,
scoped-secrets migrations, and egress allowlists across hundreds of repos *as reviewable PRs*, ingest
the Data Stream, report coverage/posture/drift against the native controls, and export it all as
compliance evidence. GitHub gives every repo a primitive; nobody makes 400 repos *adopt and stay
converged on* those primitives with a GitOps trail. That is a durable, platform-engineering-flavored
center that gets *stronger* as GitHub ships more primitives. This is the single biggest bet — it turns
the roadmap from a threat into ActionsPlane's distribution channel.

**2. The governance-plane MCP server: make ActionsPlane the safe interface between AI agents and your
CI fleet.** Expose posture, findings, drift, blast-radius, and campaign *proposals* over MCP — read by
default, every write gated as an unmergeable PR. As coding agents (Claude Code, Copilot, Cursor)
increasingly *author and trigger workflows*, two needs converge: agents need a trustworthy way to ask
"is this workflow change safe / least-privilege / pinned?" and orgs need to *govern* agent-authored CI
sprawl (the 2026 multi-agent prompt-injection incidents). ActionsPlane's "all edits via PR, never
direct-to-main" thesis is *exactly* the right safety posture for agentic writes. An MCP server that an
agent can call to propose-but-not-merge a converge-PR — plus an audit of which workflow changes were
AI-authored — is novel, on-trend, and a strong Staff/Principal signal that lands the AI + supply-chain
intersection without chasing hype.

**3. The continuously-converging "posture bot" (Renovate for governance, not dependencies).** Move
beyond one-shot campaigns to a scheduled, policy-driven bot that keeps the fleet converged: re-runs
audits, opens/refreshes converge-PRs for new drift, respects an org policy file (dry-run→enforce),
de-dupes against open PRs, and backs off on human edits. Renovate owns continuous *dependency* PRs;
nobody owns continuous *posture/governance* PRs (pin drift, permission creep, secret over-scoping,
template drift, lock-file staleness). This is the operational-maturity step that separates "a tool that
can fix once" from "a control plane that keeps you fixed," and it composes naturally with bets 1 and 2.

---

## (e) Sources

GitHub platform / 2026 roadmap (primary):
- [What's coming to our GitHub Actions 2026 security roadmap (GitHub Blog, 2026-03-26)](https://github.blog/news-insights/product-news/whats-coming-to-our-github-actions-2026-security-roadmap/) — dependency lock files, policy-driven execution + evaluate mode, scoped secrets, Actions Data Stream, native egress firewall.
- [Roadmap community discussion #190621](https://github.com/orgs/community/discussions/190621)
- [Immutable subject claims for GitHub Actions OIDC tokens (Changelog, 2026-04-23)](https://github.blog/changelog/2026-04-23-immutable-subject-claims-for-github-actions-oidc-tokens/)
- [Immutable releases (GitHub Docs)](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
- [Securing the open source supply chain across GitHub (GitHub Blog)](https://github.blog/security/supply-chain-security/securing-the-open-source-supply-chain-across-github/)

zizmor / scanners (find→fix gap):
- [zizmor auto-fixes overview (Mostafa Moradian)](https://mostafa.dev/github-actions-security-04cd056ea9c4) — autofix coverage by version (v1.10–v1.13).
- [Securing GitHub Actions with William Woodruff (Open Source Security, 2025-05)](https://opensourcesecurity.io/2025/2025-05-securing-github-actions-william-woodruff/)
- [Unpacking Security Scanners for GitHub Actions Workflows (arXiv 2601.14455v2, 2026)](https://arxiv.org/html/2601.14455v2) — zizmor ~10 weaknesses / 23 rules, broadest coverage.
- [How to detect vulnerable GitHub Actions at scale with zizmor (Grafana)](https://grafana.com/blog/how-to-detect-vulnerable-github-actions-at-scale-with-zizmor/)

Incidents / threat landscape:
- [Popular GitHub Action tags redirected to imposter commit (The Hacker News, 2026-05)](https://thehackernews.com/2026/05/github-actions-supply-chain-attack.html)
- [OpenSSF — Maintainers' guide after tj-actions and reviewdog (2025-06)](https://openssf.org/blog/2025/06/11/maintainers-guide-securing-ci-cd-pipelines-after-the-tj-actions-and-reviewdog-supply-chain-attacks/)
- [Trivy GitHub Actions supply chain compromise (Snyk)](https://snyk.io/articles/trivy-github-actions-supply-chain-compromise/)

AI-agent CI risk:
- [AI Coding Agent Prompt Injection: CI/CD Risk 2026 (Waxell)](https://waxell.ai/blog/ai-coding-agent-prompt-injection-cicd-2026) — single PR title hijacking Claude/Gemini/Copilot agents; CVE-2025-53773 (CVSS 9.6).
- [Top AI Security Vulnerabilities to Watch in 2026 (Cycode)](https://cycode.com/blog/ai-security-vulnerabilities/)
- [AI Agent Sprawl: Security Risks and Governance (Reco)](https://www.reco.ai/learn/ai-agent-sprawl)

MCP:
- [Model Context Protocol specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25)
- [Securing MCP — Coalition for Secure AI](https://www.coalitionforsecureai.org/securing-the-ai-agent-revolution-a-practical-guide-to-mcp-security/) — read-only-by-default, scoped perms; ~13k MCP servers in 2025; Asana tenant-isolation flaw.

FinOps / runners:
- [Pricing changes for GitHub Actions (GitHub)](https://github.com/resources/insights/2026-pricing-changes-for-github-actions) / [Changelog 2025-12-16](https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/) — ~39% hosted price cut Jan 2026; $0.002/min self-hosted platform charge Mar 2026.
- [GitHub self-hosted runner cost increase & alternatives (Northflank)](https://northflank.com/blog/github-pricing-change-self-hosted-alternatives-github-actions)
- [Optimizing self-hosted runner costs (WarpBuild)](https://www.warpbuild.com/blog/optimizing-self-hosted-runner-costs)

SLSA / SBOM / VEX:
- [SLSA Provenance Part 3: Adoption Challenges (Legit Security)](https://www.legitsecurity.com/blog/slsa-provenance-blog-series-part3-challenges-of-adopting-slsa-provenance)
- [Analyzing Challenges in Deployment of SLSA (arXiv 2409.05014)](https://arxiv.org/pdf/2409.05014)
- [npm provenance GA (Sigstore Blog)](https://blog.sigstore.dev/npm-provenance-ga/)
- [CISA's 2025 SBOM Minimum Elements (Sonatype)](https://www.sonatype.com/blog/what-federal-agencies-need-to-know-about-cisas-2025-sbom-minimum-elements)
- [SBOM & VEX Explained (we45)](https://www.we45.com/post/sbom-vex-explained)

Policy-as-code / governance:
- [Building org-wide governance for CI/CD with GitHub Actions (GitHub Blog)](https://github.blog/enterprise-software/devops/building-organization-wide-governance-and-re-use-for-ci-cd-and-automation-with-github-actions/)
- [Top Policy-as-Code tools 2026 (Spacelift)](https://spacelift.io/blog/policy-as-code-tools)

Engineering best practices (webhooks/async):
- [Webhooks at Scale: idempotent, replay-safe, observable (DEV)](https://dev.to/art_light/webhooks-at-scale-designing-an-idempotent-replay-safe-and-observable-webhook-system-7lk)
- [How to Implement Webhook Idempotency (Hookdeck)](https://hookdeck.com/webhooks/guides/implement-webhook-idempotency) — delivery-ID dedup (`X-GitHub-Delivery`), verify→enqueue→ACK.
- [At-Least-Once vs Exactly-Once Webhook Delivery (Hookdeck)](https://hookdeck.com/webhooks/guides/webhook-delivery-guarantees)

### Caveats / unverified
- **zizmor autofix version mapping** comes from a single practitioner blog (Moradian); the *existence*
  of autofix is well-corroborated, but exact per-version rule lists should be confirmed against
  `docs.zizmor.sh` changelog before citing precisely.
- **GitHub 2026 roadmap items are previews/targets (3–9 mo), not GA** — treat dependency lock files,
  scoped secrets, Data Stream, and egress firewall as *announced direction*, subject to change. The
  OIDC immutable-claims and custom-property-claims items *are* shipped/dated.
- **Pricing figures** (~39% cut, $0.002/min) are from GitHub's own pricing page + vendor blogs;
  directionally solid, confirm exact numbers/SKUs before quoting in any deliverable.
- **AI-agent incident specifics** (CVE-2025-53773 CVSS 9.6; the triple-agent PR-title hijack) come from
  vendor/security blogs; the CVE is real, the multi-agent demo should be treated as illustrative.
- The arXiv scanner-comparison numbers (10 weaknesses / 23 rules) are from the paper abstract via
  search snippet; verify against the full PDF if used as a hard figure.
