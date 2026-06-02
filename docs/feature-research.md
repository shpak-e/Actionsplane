# ActionsPlane — Feature Research

> Status: research only (no code). Generated 2026-05-24. Horizon: 2025–2026 sources.
> Scope: find what is *missing or emerging* relative to the current plan, and what best
> demonstrates **platform-engineering + supply-chain-security** depth for a Staff/Principal
> portfolio narrative.

ActionsPlane's thesis is the **observe + audit + edit** triangle over GitHub Actions across
many repos, via a GitHub App, with all edits as PRs (never direct-to-main). The research below
confirms that thesis holds: every incumbent owns one or two corners of the triangle, none owns
all three *with org-wide bulk PR-based remediation as the primary surface*.

---

## (a) Competitive landscape

| Tool / Capability | What it does today (2025–2026) | Corner(s) owned | Gap ActionsPlane can own |
|---|---|---|---|
| **StepSecurity** (harden-runner + platform) | Runtime EDR for runners (egress/file/process monitoring, baselining, lockdown, global block list via 24×7 SOC); policy store at workflow/repo/org/cluster scope; **build-log secret scanning**; ARC support. Commercial SaaS core. | Audit (runtime), partial Edit (their own actions/policy store) | OSS, **static + config-time** org-wide audit and **PR-based remediation** of the YAML itself, not a runtime agent. Self-hosted, no SaaS lock-in. |
| **zizmor** (zizmorcore, Trail of Bits) | Best-in-class **static analyzer** for workflows (~24+ audits: template injection, mutable-tag pins, known-vuln actions, excessive perms). SARIF output, offline-first, YAML-anchor aware (Sept 2025+). CLI/CI. | Audit (static) | zizmor finds; it does **not fix at scale, observe runs, track drift, or roll up org metrics**. ActionsPlane can *consume zizmor SARIF* and turn findings into bulk converge-PRs. |
| **octoscan** (Synacktiv) | Go static scanner: dangerous checkout, expression injection, repo-jacking, OIDC misuse, runner-label abuse; can pull remote workflows; JSON/SARIF. | Audit (static, offensive lens) | Same gap as zizmor — single-repo, find-only, no remediation/observe/drift. Another SARIF feed to ingest. |
| **Renovate** | Auto-pins actions to SHA (`helpers:pinGitHubActionDigests[ToSemver]`), keeps SHAs current via PRs. | Edit (pin maintenance only) | Renovate pins *dependencies*; it has **no audit/observe surface**, no permission/secret/runner audit, no drift-to-template, no run dashboards. ActionsPlane = the governance layer Renovate lacks. |
| **Dependabot** | Now supports SHA-pin + immutable-reference updates for actions; security alerts. | Edit (pin/version), partial Audit | Same: dependency-centric, no org observe/drift/permission posture. |
| **Datadog CI Visibility / Test Optimization** | Run/test telemetry, flaky-test management (quarantine, auto-retry, early flake detection), test-impact analysis, PR summaries; Bits AI flake-fix PRs. SaaS, $$. | Observe + Metrics (test-centric) | DD is observability-only and expensive SaaS; **no audit, no pin/permission remediation, no supply-chain posture**. ActionsPlane targets the security/governance gap DD ignores. |
| **Trunk / Mergify** | Merge-queue orchestration: parallel/scoped queues, batching+bisect, two-step CI, queue analytics. | Metrics/throughput (merge-time) | Orthogonal — queue throughput, not workflow governance. Possible *complement*, not competitor. |
| **GitHub native — rulesets / required workflows** | Org & enterprise rulesets (GA, expanded to Team plan June 2025); **require workflows** to pass before merge; Actions **policy can now block + require SHA-pinning** (Aug 2025). | Audit/enforce (policy gate) | Native enforcement is coarse (allow/deny, pin-required) and **gives no cross-repo visibility, no remediation PRs, no drift diff, no metrics rollup**. ActionsPlane can *configure & report on* rulesets, and fix the long tail rulesets only block. |
| **GitHub native — secret scanning / code scanning / security APIs** | Secret scanning across git history; SARIF ingestion to Security tab; org Actions usage-metrics permission. | Audit (secrets/code), Observe (usage) | Native is per-repo siloed; ActionsPlane aggregates org-wide and ties findings to *workflow* objects + remediation. |
| **GitHub native — enhanced billing / usage API** | Consolidated usage endpoint (product/SKU/minutes/price), budgets, cost centers (Nov 2025); product-specific billing APIs retired Sept 2025. | Metrics (raw) | Raw billing data only; **no per-workflow/per-team attribution, no anomaly detection, no cost-of-flake**. ActionsPlane turns the feed into FinOps. |
| **OPA / Conftest / Rego** | Generic policy-as-code engine; used in CI gates over structured config. | (toolkit, not a product) | No GitHub-Actions-specific model, no UI, no org rollout. ActionsPlane can *embed* a Rego/CEL engine as its policy core. |
| **OpenSSF Scorecard** | Repo security-health scoring incl. some Actions checks (pinning, token perms); GitHub Action + SARIF. | Audit (scoring) | Scorecard scores; doesn't remediate or give an editing/observe plane. Good *signal source* to surface and converge against. |
| **slsa-verifier / cosign / Sigstore** | Verify SLSA provenance & in-toto attestations; cosign verifies GitHub Artifact Attestations / npm provenance. | (verification toolkit) | No fleet view of *which repos produce/verify attestations*. ActionsPlane can report attestation coverage org-wide. |

**Bottom line gap:** the market splits into (1) find-only static linters (zizmor, octoscan), (2)
runtime EDR (StepSecurity), (3) dependency pinners (Renovate/Dependabot), (4) test/observe SaaS
(Datadog), and (5) coarse native enforcement (rulesets). **Nobody offers a self-hosted OSS plane
that observes runs, audits posture, detects drift from a canonical template, and ships bulk
PR-based fixes — with security findings (incl. ingested zizmor/Scorecard SARIF) converted directly
into converge-PRs.** That is ActionsPlane's defensible center.

---

## (b) Prioritized feature backlog

Sorted high→low by value/effort. Effort is relative (S/M/L). Items marked **NEW** are not in the
current plan; others sharpen or extend planned features.

| # | Feature | Category | Value | Effort | Differentiation |
|---|---|---|---|---|---|
| 1 | **Ingest zizmor/octoscan/Scorecard SARIF → unified findings → one-click converge-PRs** **NEW** | security | High | M | Turns best-in-class find-only linters into *fixers*; nobody bridges find→fix at org scale. |
| 2 | **PR-time workflow linting as a GitHub Check** (App posts annotations on workflow PRs) **NEW** | audit | High | S | Shift-left; uses Checks API. Native rulesets only block, don't explain/fix. |
| 3 | Pin-to-SHA + bump-pins bulk PRs (planned) — extend with **immutable-reference / GHCR-action awareness** | edit | High | S | Post-`tj-actions` (CVE-2025-30066) this is table-stakes but the #1 demo. Add immutable-action support GitHub shipped 2025. |
| 4 | **Policy-as-code gate over workflows (Rego/CEL), versioned, dry-run + enforce modes** **NEW** | security | High | M | Embeds OPA/CEL with an Actions-specific data model + UI; OPA is a toolkit, not a product. Staff-level signal. |
| 5 | Permission/least-privilege audit (planned) → **auto-generate minimal `permissions:` PRs** | audit/edit | High | M | Computes least-privilege from observed token usage, not guesswork. |
| 6 | **Org-wide supply-chain posture scorecard** (pin %, perms, attestation coverage, harden-runner adoption, Scorecard) **NEW** | security | High | M | Single executive view across all repos; no OSS tool aggregates this. |
| 7 | Drift detection vs canonical template (AST diff) + converge-PRs (planned) — keep central | drift | High | L | The "golden workflow" GitOps story; rare and very Staff/Principal. |
| 8 | **Blast-radius / impact analysis of a workflow change** (which repos/teams/reusable-workflow consumers are affected) **NEW** | drift | High | M | Reusable-workflow dependency graph; unique change-safety narrative. |
| 9 | **Reusable-workflow catalog + adoption tracker** (who consumes which reusable WF + version) **NEW** | observe | Med-High | M | Internal "marketplace"/inventory; supports migrate-to-reusable edits already planned. |
| 10 | Cross-repo run dashboard + failure clustering + top offenders (planned) | observe | High | M | Core observe; well-trodden, so differentiate via security overlay. |
| 11 | **Cost-anomaly detection + per-team/per-workflow attribution + "cost of flake"** (enhanced billing API) **NEW** | metrics | Med-High | M | Native billing is raw data; FinOps-for-CI on top of Nov-2025 usage API. |
| 12 | **Attestation/provenance coverage report** (which repos emit SLSA provenance / artifact attestations; verify with cosign/slsa-verifier) **NEW** | security | Med-High | M | SLSA maturity dashboard; high-signal supply-chain depth. |
| 13 | **Compliance evidence export** (CISA SSDF / EO 14028 artifacts: pin status, perms, provenance, SBOM-for-CI) **NEW** | security | Med | M | Maps technical posture to a recognized compliance form; rare and enterprise-credible. |
| 14 | Secret-usage audit (planned) → **+ workflow-log secret-leak scanning** (post-run) **NEW** | audit | Med | M | StepSecurity charges for this; OSS version is a strong draw. Respect log-retention/PII. |
| 15 | Inject harden-runner / hardening steps bulk PRs (planned) | edit | Med | S | Easy win; pairs with posture scorecard. |
| 16 | Deprecation scanner (planned) — extend to **runner-version EOL** (self-hosted <v2.329.0 blocked Mar 2026) | audit | Med | S | Timely; ties to the Mar-2026 ARC/runner cutoff. |
| 17 | **Self-hosted runner / ARC fleet posture** (versions, ephemerality, scope, labels) **NEW** | security | Med | M | ARC ships *no* built-in security; reporting on fleet hygiene is unowned in OSS. |
| 18 | Concurrency / runner / publisher-trust audits (planned) | audit | Med | S | Round out the audit suite; mostly read-only. |
| 19 | Notifications (Slack/webhook/email) + CLI (planned) | platform | Med | S | Distribution surface; CLI matters for the terminal-ops portfolio story. |
| 20 | Minutes/cost, queue time, cache-hit, success trends (planned) | metrics | Med | M | Standard metrics; commoditized by Datadog — lead with security, not these. |
| 21 | **Configure & report on GitHub org rulesets / required-workflows + Actions block/pin policy** **NEW** | platform | Med | M | Make ActionsPlane the *control surface* for native enforcement, fixing the long tail rulesets only block. |

---

## (c) Headline differentiators (portfolio narrative)

Pick 3–5 to lead the README and demo. These maximize Staff/Principal signal.

1. **Find→Fix bridge: SARIF in, converge-PRs out.** Ingest zizmor/octoscan/Scorecard findings,
   dedupe into a unified model, and auto-open *bulk PR-based fixes* org-wide. Every other tool stops
   at "here's a finding." This is the single strongest differentiator — it operationalizes the whole
   existing OSS security-linter ecosystem instead of competing with it.

2. **Policy-as-code governance plane for Actions (Rego/CEL), dry-run → enforce.** A versioned,
   org-wide policy engine over workflows with an Actions-specific data model and impact preview.
   Demonstrates platform-engineering maturity (GitOps, progressive rollout) that raw OPA or coarse
   GitHub rulesets don't.

3. **Org supply-chain posture scorecard + compliance evidence export.** One view of pin %,
   least-privilege coverage, attestation/SLSA coverage, runner hygiene — exportable as CISA
   SSDF / EO 14028 evidence. Connects deep technical posture to executive/compliance language.

4. **Drift-to-golden-workflow with blast-radius analysis.** Canonical templates + AST diff +
   "which repos/teams/reusable-workflow consumers does this change touch?" The change-safety and
   reusable-workflow dependency-graph story is distinctly Staff/Principal.

5. **All edits are PRs, never direct-to-main — by design.** The auditable, reviewable,
   GitOps-native remediation model is itself a differentiator versus runtime agents (StepSecurity)
   and silent auto-updaters. Lead with this as the trust thesis.

---

## (d) Sources

Competitive landscape:
- [StepSecurity Harden-Runner docs](https://docs.stepsecurity.io/github-actions/harden-runner)
- [step-security/harden-runner (GitHub)](https://github.com/step-security/harden-runner)
- [StepSecurity — scan build logs for secrets](https://www.stepsecurity.io/blog/scan-github-actions-build-logs-for-secrets-with-stepsecuritys-new-feature)
- [StepSecurity — Harden-Runner for ARC](https://www.stepsecurity.io/blog/introducing-harden-runner-for-kubernetes-based-self-hosted-actions-runners)
- [zizmorcore/zizmor (GitHub)](https://github.com/zizmorcore/zizmor) · [docs.zizmor.sh](https://docs.zizmor.sh/)
- [Trail of Bits — "We hardened zizmor's static analyzer" (2026)](https://blog.trailofbits.com/2026/05/22/we-hardened-zizmors-github-actions-static-analyzer/)
- [Grafana — detect vulnerable Actions at scale with zizmor](https://grafana.com/blog/2025/06/26/how-to-detect-vulnerable-github-actions-at-scale-with-zizmor/)
- [synacktiv/octoscan (GitHub)](https://github.com/synacktiv/octoscan)
- [Renovate — GitHub Actions manager / digest pinning](https://docs.renovatebot.com/modules/manager/github-actions/)
- [Datadog Test Optimization docs](https://docs.datadoghq.com/tests/) · [Flaky Tests Management](https://docs.datadoghq.com/tests/flaky_management/)
- [Datadog — monitor GitHub Actions with CI Visibility](https://www.datadoghq.com/blog/datadog-github-actions-ci-visibility/)
- [Mergify — CI orchestration beyond merge queue](https://mergify.com/blog/github-merge-queue-was-step-one-real-ci-orchestration-comes-next/)
- [Trunk vs GitHub Merge Queue](https://trunk.io/trunk-vs-github-merge-queue)

GitHub platform capabilities (2025):
- [Changelog — Actions policy supports blocking + SHA-pinning (Aug 2025)](https://github.blog/changelog/2025-08-15-github-actions-policy-now-supports-blocking-and-sha-pinning-actions/)
- [Changelog — Org rulesets for Team plans (Jun 2025)](https://github.blog/changelog/2025-06-16-organization-rulesets-now-available-for-github-team-plans/)
- [GitHub — enforcing reliability by requiring workflows](https://github.blog/enterprise-software/ci-cd/enforcing-code-reliability-by-requiring-workflows-with-github-repository-rules/)
- [Changelog — billing API budgets & usage (Nov 2025)](https://github.blog/changelog/2025-11-03-manage-budgets-and-track-usage-with-new-billing-api-updates/)
- [Changelog — product-specific billing APIs closing down (Sep 2025)](https://github.blog/changelog/2025-09-26-product-specific-billing-apis-are-closing-down/)
- [Docs — Enhanced billing / usage REST API](https://docs.github.com/en/rest/billing/usage?apiVersion=2026-03-10)
- [actions/publish-immutable-action (GHCR OCI actions)](https://github.com/actions/publish-immutable-action)
- [Docs — Artifact attestations / build provenance](https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds)
- [actions/attest-build-provenance](https://github.com/actions/attest-build-provenance)
- [actions/actions-runner-controller (ARC)](https://github.com/actions/actions-runner-controller) · [ARC security docs](https://docs.github.com/en/actions/concepts/runners/actions-runner-controller)
- [Self-hosted runner upgrade deadline (Mar 16 2026)](https://devactivity.com/posts/apps-tools/github-actions-self-hosted-runners-dont-get-blocked-upgrade-now-for-peak-software-development-efficiency/)

Supply-chain & compliance:
- [SLSA framework via slsa-verifier](https://github.com/slsa-framework/slsa-verifier)
- [Sigstore — cosign verify of GitHub Artifact Attestations / npm provenance](https://blog.sigstore.dev/cosign-verify-bundles/)
- [OpenSSF Scorecard](https://scorecard.dev/) · [ossf/scorecard-action](https://github.com/ossf/scorecard-action)
- [Secure Pipelines — SLSA to in-toto attestations](https://secure-pipelines.com/ci-cd-security/artifact-provenance-attestations-slsa-in-toto/)
- [CISA — Secure Software Development Attestation Form](https://www.cisa.gov/secure-software-attestation-form)
- [Anchore — SSDF attestation overview](https://anchore.com/blog/an-overview-ssdf-attestation-form/)

Policy-as-code & incidents:
- [OPA in CI/CD](https://www.openpolicyagent.org/docs/cicd) · [open-policy-agent/conftest](https://github.com/open-policy-agent/conftest)
- [Checkmarx — CVE-2025-30066 (tj-actions/changed-files)](https://checkmarx.com/zero-post/compromised-github-actions-leading-to-credential-leaks/)
- [Snyk — 28M+ credentials leaked on GitHub in 2025](https://snyk.io/articles/state-of-secrets/)
- [TruffleHog (secret scanning)](https://github.com/trufflesecurity/trufflehog)

### Caveats / not fully verified
- CEL (vs Rego) for workflow policy: OPA/Conftest/Rego is well-documented; a dedicated CEL-over-workflows
  product was **not** found. Treat CEL as an implementation option, not an existing precedent.
- "Cerberus/Cerber" for GitHub Actions: **could not verify** a notable OSS tool by that name in this
  space within 2025–2026 sources; likely a naming confusion. Excluded rather than fabricated.
- StepSecurity exact OSS-vs-paid feature boundaries shift; treat the paid/SaaS split as approximate.
