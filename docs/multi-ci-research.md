# Multi-CI Research: Should ActionsPlane Extend Beyond GitHub Actions?

> Research-only assessment (no code). Question: should ActionsPlane port its
> **observe + audit + drift + edit** wedge to other CI/CD stacks, and if so which,
> in what order, and how — without diluting the GitHub Actions focus.
> Sources are 2025–2026; capabilities are flagged where unverified.

**TL;DR:** Stay GitHub-only through v1. The only stack worth a real port is
**GitLab CI/CD**, and only *after* the GHA wedge is mature, because it is the one
target where all four pillars map cleanly onto well-known APIs and a YAML format.
**Explicitly do not touch Jenkins** — its pipeline format is Groovy code, not data,
which breaks the AST + ruamel round-trip model the whole product is built on.

---

## (a) Market Share & Momentum (2025–2026)

Primary source: **JetBrains "State of CI/CD 2025"** survey (805 respondents). All
percentages below are *usage*, not exclusive share; the survey explicitly notes
**32% of orgs use 2 tools and 9% use 3+**, so columns do not sum to 100%.

| Stack | Org usage | Personal usage | Momentum (2025–26) | Multi-repo sprawl + supply-chain pain? |
|---|---|---|---|---|
| **GitHub Actions** | **41%** (most-used org tool) | **62%** (dominant) | Rising; ~68% of OSS GitHub projects (secondary sources). Slow enterprise displacement of legacy. | **Yes — the bullseye.** Org-wide `uses:` sprawl, `tj-actions` retargeting class of attack, pin %. |
| **Jenkins** | ~28–33% (top in large/Fortune 500) | Low | **Declining** (cited ~-8% YoY in secondary sources) but entrenched; mission-critical legacy "don't migrate" inertia. | Yes (shared-libraries + ~70 plugin CVEs in 2025) — but format makes it the *hardest* to serve. |
| **GitLab CI/CD** | ~19% | Moderate | **Fastest enterprise growth** (secondary: +~34% YoY). DevSecOps-native. | **Yes.** `include:` sprawl, the new **CI/CD Components** catalog, same pin-to-SHA story. |
| **Azure Pipelines / Azure DevOps** | Notable in Microsoft shops | Low | Flat/slow; Microsoft steering new work toward GitHub. | Some — YAML templates, but shrinking new-project share. |
| **Bitbucket Pipelines** | Niche; strong in Atlassian/Jira shops | Low | Flat. | Limited — smaller per-account repo counts. |
| **TeamCity** | ~7% org / ~2% personal | Low | Stable niche (on-prem, JetBrains shops). | Low. |
| **CircleCI** | Single digits | Low | Declining vs GHA/GitLab; post-2023 breach reputational drag. | Moderate — **orbs** are a real pinning surface, but shrinking footprint. |
| Tekton / Argo Workflows | K8s-platform niche | — | Growing inside platform teams, but CD/pipeline-engine, not VCS-attached CI. | Different shape — CRDs in cluster, not files in many repos. |
| Drone / Harness, Buildkite, Travis | Small / shrinking (Travis effectively legacy) | — | Travis declining; Buildkite/Harness niche-enterprise. | Low priority. |

**Read:** GitHub Actions has *both* the largest footprint **and** the sharpest
supply-chain pain that ActionsPlane was built for. GitLab CI is the only credible
"add next" candidate — fastest-growing, genuinely multi-repo, and supply-chain-aware
(GitLab itself published 2026 guidance to SHA-pin CI/CD Components). Jenkins is big
but structurally hostile to the product's design. Everything else is a long tail.

---

## (b) Per-Stack Fit Matrix (Observe / Audit / Drift / Edit)

Scored Easy / Medium / Hard against ActionsPlane's existing design (typed AST +
ruamel round-trip + GitHub-App-style auth + all-edits-via-PR).

### GitLab CI/CD
| Pillar | Fit | Why |
|---|---|---|
| Observe | **Easy** | Pipeline webhooks fire on start/success/fail/cancel with stage + commit payloads; full Pipelines API for backfill. Maps 1:1 to `workflow_run`/`workflow_job`. |
| Audit | **Medium** | Real analogous surface: `include:` (local/remote/template/component) + the **CI/CD Components** catalog. GitLab's own 2026 guidance = SHA-pin components, avoid `~latest`/mutable refs. Pin classifier + permission/`rules:` analysis port; semantics differ from `uses:`. |
| Drift | **Easy–Medium** | Same "copy-pasted pipeline across repos" problem; `include:` and Components are the templating mechanism, so a canonical-template diff maps well. |
| Edit | **Easy** | Mature Merge Requests API (incl. `python-gitlab`); the all-edits-via-MR model is a direct analogue of all-edits-via-PR. |

### Jenkins
| Pillar | Fit | Why |
|---|---|---|
| Observe | **Medium** | No native rich webhooks by default; relies on plugins/API polling. Doable but plugin-dependent and inconsistent across installs. |
| Audit | **Hard** | Supply-chain surface is **shared libraries + ~thousands of community plugins** (70+ CVEs in 2025). No clean declarative "uses" graph to pin. |
| Drift | **Hard** | `Jenkinsfile` is **Groovy code**, not data — structural drift requires parsing a Turing-complete DSL. |
| Edit | **Hard** | Round-trip editing of Groovy with comment/format preservation is not the ruamel/YAML model at all. **This is the deal-breaker.** |

### Azure Pipelines (Azure DevOps)
| Pillar | Fit | Why |
|---|---|---|
| Observe | **Medium** | Service hooks + REST runs API exist; auth is Azure DevOps PAT/Entra, a different identity model from GitHub Apps. |
| Audit | **Medium** | YAML with templates; some pinning/task-version surface, but a smaller and shrinking new-project base. |
| Drift | **Medium** | YAML templates give a real templating mechanism; AST approach ports. |
| Edit | **Medium** | PR-based via Azure Repos API, but a whole second auth + repo-host integration to build. |

### CircleCI
| Pillar | Fit | Why |
|---|---|---|
| Observe | **Medium** | Webhooks + API exist, but footprint is shrinking. |
| Audit | **Medium** | **Orbs** are a genuine pin/trust surface (reusable YAML packages) analogous to actions. |
| Drift | **Medium** | `config.yml` is YAML; orbs are the templating layer. AST ports. |
| Edit | **Hard-ish in practice** | CircleCI config lives in the VCS repo (usually GitHub), so edits route back through GitHub PRs anyway — meaning the *incremental* value over the existing GHA path is mostly a second parser, not a new edit path. Low ROI. |

### Tekton / Argo Workflows
| Pillar | Fit | Why |
|---|---|---|
| Observe | **Hard** | State lives as Kubernetes CRDs in a cluster, watched via the K8s API — not VCS webhooks. Different ingestion model. |
| Audit | **Medium** | Tasks/Pipelines are pinnable (bundles/digests), but it's a cluster-RBAC problem, not a repo-sprawl one. |
| Drift | **Medium** | Declarative YAML CRDs diff cleanly *if* you adopt a K8s-watch model. |
| Edit | **Hard** | "Edit via PR" doesn't fit live in-cluster CRDs; GitOps repos sometimes do, inconsistently. |

---

## (c) Recommended Sequence

**1. Finish the GitHub Actions wedge first (do not split focus).**
The differentiator per the plan is the **find→fix bridge** (SARIF ingest →
unified findings → converge-PRs). No OSS tool does observe+audit+drift+edit for
*even one* stack. Going multi-stack before that loop is proven dilutes the
strongest, most defensible story and the cleanest Staff/Principal narrative.

**2. Add GitLab CI/CD next — and only after the GHA edit pillar (Phase 4) is solid.**
Rationale: it is the single target where **all four pillars score Easy/Medium**
on APIs and formats that already exist (pipeline webhooks, Pipelines API, MR API,
`include:`/Components, YAML). It's the fastest-growing enterprise stack and shares
the *exact* supply-chain framing GitLab itself now publishes (SHA-pin Components).
This is also where the **Provider abstraction earns its keep** — porting to a
second YAML/PR/webhook stack validates the design without fighting the format.

**3. Distant third (optional, only if a real user pulls): Azure Pipelines**, because
it's YAML + template-based and serves the Microsoft-shop segment. Treat as
demand-driven, not roadmap.

**Explicitly DO NOT:**
- **Jenkins** — Groovy `Jenkinsfile` is imperative code, not declarative data.
  The AST-diff + ruamel round-trip + "convert duplicated workflow to reusable
  call" operations cannot port. Serving it would mean a *second product*
  (a Groovy parser/rewriter), not a port. Biggest market with the worst fit.
- **CircleCI** as a distinct edit target — its config lives in the GitHub repo,
  so edits already flow through the existing GHA PR path; a separate provider
  adds a parser for little incremental value.
- **Tekton / Argo** — cluster-CRD model, not VCS-repo-sprawl model; wrong shape
  for "observe runs + edit files via PR across N repos."

**Framing for the portfolio:** "GitHub Actions deeply, GitLab CI as the proof the
architecture generalizes, and a documented, reasoned refusal to chase Jenkins"
reads as *more* senior than "supports everything." The non-goal is the signal.

---

## (d) Provider Abstraction Sketch

The plan already claims the AST + client layers are kept "provider-shaped." That
claim is **directionally correct and worth committing to**, with one caveat: the
*pipeline parser* must be a first-class per-provider component, not an afterthought,
because format semantics (not just transport) differ the most.

A clean internal abstraction — five seams, each provider implements all five:

```python
class Provider(Protocol):
    name: str  # "github" | "gitlab" | ...

    # 1. AUTH — issue scoped, short-lived creds per install/group.
    #    GitHub App installation tokens; GitLab group/project tokens or OAuth.
    def auth(self, install_ref: InstallRef) -> ScopedClient: ...

    # 2. RUN-INGEST — normalize native run events into the existing
    #    event-sourced model (workflow_runs / workflow_jobs).
    #    GitHub: workflow_run/workflow_job webhooks.
    #    GitLab: Pipeline + Job events.
    def normalize_run_event(self, raw: dict) -> RunEvent: ...
    def backfill_runs(self, repo: RepoRef, since: datetime) -> Iterable[RunEvent]: ...

    # 3. FILE-FETCH — read pipeline files at a ref (+ resolve includes/components).
    def fetch_pipeline_files(self, repo: RepoRef, ref: str) -> list[PipelineFile]: ...

    # 4. PR/MR-CREATION — the universal "all edits via review" path.
    #    GitHub PR == GitLab MR == Bitbucket PR. Branch + change + review request.
    def open_change_request(self, repo: RepoRef, branch: str,
                            edits: list[FileEdit], meta: ChangeMeta) -> ChangeRef: ...

    # 5. PIPELINE PARSER — native config -> shared typed AST (Pydantic),
    #    round-trippable for edits. THE risk-concentrating seam.
    def parser(self) -> PipelineParser: ...
```

Key design notes:
- **Keep the existing typed AST as the lingua franca.** Provider parsers normalize
  into a shared `Pipeline / Job / Step` model so audit/drift/edit engines stay
  provider-agnostic. Provider-specific concepts (`uses:` vs `include:`/Components
  vs orbs) become typed sub-variants, not engine branches.
- **`PipelineParser` is where YAML-vs-not-YAML lives.** GitHub/GitLab/Azure/CircleCI
  all implement it over ruamel (round-trip preserved). Jenkins *cannot* implement
  it under the same contract — which is exactly why the abstraction's existence is
  what tells you Jenkins is out of scope. The interface makes the boundary honest.
- **Auth and transport differ but are shallow**; the format/AST seam is deep. Budget
  effort accordingly: a GitLab port is ~80% parser + finding-semantics work, ~20%
  auth/webhook plumbing.
- **Critique of the current claim:** "client layers kept provider-shaped" is fine
  for auth/ingest/file/PR, but the plan should explicitly elevate the *parser* and
  the *finding taxonomy* (pin states, permissions, publisher trust) to provider-
  pluggable, since those are GHA-specific today (`uses:`, `GITHUB_TOKEN`,
  Marketplace publishers) and won't survive contact with GitLab unchanged.

---

## (e) Honest Risks

1. **Jenkinsfile is Groovy, not YAML (the headline risk to any "multi-CI" claim).**
   The entire edit/drift design assumes declarative, round-trippable config. Groovy
   is imperative code. Any promise to "support Jenkins" silently means building a
   Groovy parser/rewriter — a separate, much harder product. **Verified** via Jenkins
   docs (Declarative + Scripted both Groovy DSL) and the long-running interest in a
   YAML-pipeline plugin precisely because Groovy is hard to author/modify.
2. **Finding taxonomy is GitHub-shaped today.** Pin classifier (`sha/tag/branch/
   unpinned`), `GITHUB_TOKEN` permission audit, and Marketplace publisher trust are
   GHA concepts. GitLab needs a re-mapped taxonomy (Components catalog trust,
   `rules:`/protected-branch semantics). Underestimating this turns a "port" into a
   "rebuild." (Assessment, not a single-source fact.)
3. **Auth model fragmentation.** GitHub App installation tokens are clean; GitLab
   (group/project access tokens or OAuth), Azure (PAT/Entra), Bitbucket (app
   passwords/OAuth) each differ. Manageable but real per-provider plumbing.
4. **Webhook parity varies.** GitLab pipeline webhooks are solid (verified via
   GitLab docs). Jenkins has no rich native webhooks (plugin-dependent). Tekton has
   no VCS webhooks at all — it's a K8s-watch model.
5. **Scope-creep / focus dilution.** Going multi-stack early competes with the
   plan's own non-goal ("Supporting GitLab/Bitbucket in v1. Single-provider focus").
   The product's *whole pitch* is depth on the observe+audit+edit triangle; breadth
   before that is proven weakens it.
6. **Renovate already owns cross-platform *dependency* updates** (90+ managers across
   GitHub/GitLab/Bitbucket/Azure). ActionsPlane must stay distinct — it's
   observe+audit+drift+governance with bulk PR campaigns, **not** a dependency bot.
   A multi-CI move must not drift into Renovate's lane.
7. **"Universal CI governance" is largely a gap, not a crowd** — but that's *because*
   it's hard (format heterogeneity). Don't read the empty field as easy.

---

## (f) Sources

- [JetBrains — The State of CI/CD in 2025](https://blog.jetbrains.com/teamcity/2025/10/the-state-of-cicd/) — primary survey: GHA 41% org / 62% personal; Jenkins & GitLab enterprise weight; 32%/9% multi-tool.
- [JetBrains — Best CI/CD Tools for 2026: What the Data Actually Shows](https://blog.jetbrains.com/teamcity/2026/03/best-ci-tools/)
- [JetBrains — What Are the Security Risks of CI/CD Plugin Architectures?](https://blog.jetbrains.com/teamcity/2026/03/ci-cd-security-risks/) — Jenkins 70+ plugin CVEs in 2025.
- [GitLab Docs — Webhook events](https://docs.gitlab.com/user/project/integrations/webhook_events/) — pipeline/job event payloads.
- [GitLab Docs — Trigger pipelines with the API](https://docs.gitlab.com/ci/triggers/)
- [GitLab Docs — CI/CD components](https://docs.gitlab.com/ci/components/) — pin to SHA / release; avoid `~latest`.
- [GitLab — Pipeline security lessons from March supply chain incidents](https://about.gitlab.com/blog/pipeline-security-lessons-from-march-supply-chain-incidents/) — SHA-pin components & images.
- [GitLab Docs — Merge requests API](https://docs.gitlab.com/api/merge_requests/) / [python-gitlab MRs](https://python-gitlab.readthedocs.io/en/stable/gl_objects/merge_requests.html) — MR-as-PR edit path.
- [CircleCI Docs — Protecting against supply chain attacks](https://circleci.com/docs/guides/security/security-supply-chain/) and [CircleCI Orbs](https://circleci.com/orbs/) — orb pinning surface.
- [Jenkins — Pipeline Syntax](https://www.jenkins.io/doc/book/pipeline/syntax/) — Declarative & Scripted are Groovy DSL.
- [Jenkins — Pipeline as YAML (experimental)](https://www.jenkins.io/projects/gsoc/2020/project-ideas/pipeline-as-yaml-experiment/) — evidence Groovy is hard to author/modify.
- [Bitbucket Cloud REST API — Pull requests](https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/)
- [Renovate (Mend.io) GitHub](https://github.com/renovatebot/renovate) + [Dependabot vs Renovate: GitHub-Only vs 5 Platforms](https://appsecsanta.com/sca-tools/dependabot-vs-renovate) — cross-platform dependency lane to stay clear of.
- [OWASP — CI/CD Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/CI_CD_Security_Cheat_Sheet.html) — pin to immutable refs guidance.

> **Unverified / flagged:** YoY momentum figures (GitLab +~34%, Jenkins -~8%, GHA
> ~68% of OSS projects) come from secondary aggregator/vendor blogs, not the primary
> JetBrains survey — directionally reliable, treat exact numbers with caution. The
> "universal CI governance is a gap" claim is an inference from the absence of such a
> tool in searches, not a positive citation.
