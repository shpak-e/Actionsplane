# ActionsPlane — Staff-track Review (2026-05-31)

Scope: code under `src/actionsplane/`, `tests/`, `deploy/`, plus the captured handoff
(`docs/memory.md`), design (`plan.md`, `docs/ARCHITECTURE.md`), and the two prior security/perf
reviews (`docs/review-findings.md`, `docs/review-findings-2.md`). Treats the prior reviews as
settled where they're settled; extends or pushes back where they aren't.

Two ground rules:
1. The code-base is internally clean — pure cores, idempotent upserts, HMAC fail-closed, App
   not PAT, ruamel for edits, Pydantic for analysis. Re-litigating that wastes air.
2. The interesting questions are now (a) what the docs overstate, (b) whether the
   "GitHub-2026 evidence plane" repositioning is real or wishful, and (c) which one thing — SARIF
   find→fix, evidence-plane, or GitLab — most makes this look like Staff/Principal work.

---

## 1. Current-state delta — what the docs say vs. what the code does

### 1.1 Overstatements

**"Hardened, 83 tests green" implies live-tested. It isn't.** The `memory.md §6` gotchas concede
this — no live Postgres/Redis/GitHub anywhere. Every claim about Docker Compose, the Helm
chart, the worker cron, the SSE stream, and the executor PR path is **import-/parse-/MockTransport-
verified only.** That's fine to *say* in the README; it must not be implied in any external-
facing claim of "feature-complete + hardened." The single biggest unknown unknown in this project
is whether `arq` cron + asyncpg + the worker's `httpx.AsyncClient` lifetime + Redis pub/sub
actually compose under real load. Right now we have a hypothesis, not a verified system.

**"GitHub rate-limit robustness — concurrency is in; conditional-request caching is not"
(`memory.md §4.1`) understates what's missing.** What's in `sync/worker.py` is a
`bounded_gather` over the watched-repo list. What is *not* in the code, anywhere:
- No `If-None-Match`/ETag handling in `github/client.py` — every cron pass re-downloads
  every workflow file even when nothing changed. 304s are how GitHub *gives you free polling*
  and we ignore them. (See `github/client.py:40-110` — no headers read, no `ETag`/`Last-Modified`
  ever stored.)
- No `X-RateLimit-Remaining`/`X-RateLimit-Reset` inspection. `resp.raise_for_status()` (e.g.
  `client.py:49`) just throws on 403/429 and `bounded_gather` propagates — one rate-limited
  call sinks the entire `asyncio.gather`. There's no `Retry-After`/secondary-rate-limit backoff.
- `list_workflow_runs` and `list_workflow_files` are single-page (review-1 noted this; still
  open at `client.py:40-50`, `client.py:52-65`).
- The token cache is shared mutable state under `bounded_gather`. `client_for_installation`
  (`github/factory.py:33-46`) does a `cache.get` → optionally mint → `cache[id] = token`. With
  `fetch_concurrency=8` and a cold cache, multiple coroutines for the same installation will
  each hit `POST /app/installations/{id}/access_tokens` simultaneously. Harmless functionally
  (you get N tokens, last write wins) but it (a) wastes JWT mints, (b) generates duplicate audit
  log entries on GitHub, (c) under a flaky network can wedge if one of the mint calls fails
  mid-flight. Needs an `asyncio.Lock` per installation.

So "concurrency is in" is technically true but doesn't deliver what the prior reviews actually
asked for — and the prior reviews were right.

**"All edits via PR; never write to main." True for `pin-shas` only.** `OPERATIONS` registry
(`executor/operations.py:106-108`) has exactly one entry. `memory.md §4.9` (other directions)
lists `bump-pins / set-permissions / inject-step` as still-to-do; the README/plan §5.4 lists
five ops as if they were the deliverable. The current Phase-4 status is one op end-to-end,
which is actually fine for v1, but **don't describe Phase 4 as "✅ done"** — it's
"✅ done for one operation, the others are research."

**"GitLab provider — started" overstates how much exists.** `providers/gitlab/`
has a parser and a pin classifier for includes/components, both reusing the GitHub
vocabulary. There is **no GitLab observe path, no MR (PR-equivalent) writer, no GitLab API
client, no token model, no webhook normalizer.** It is the *audit* corner of the triangle for
one provider, off the worker path entirely. Saying "GitLab provider started" suggests the
abstraction is real; what exists is a tactical port of the pin audit. The `providers/base.py`
Protocol is the right shape, but nothing in `sync/`/`github/`/`executor/` uses it.

### 1.2 Understatements

**Apply hardening is actually solid.** `executor/campaigns.py` now stores the resolved
SHA map on `campaign.params` and reuses it on apply (line 55, 72), so the "approved diff ≠
committed diff" gap that review-2 flagged (review-2 finding "apply recomputes...") is **closed**.
Re-read the code: `apply_campaign` does `dry_run_repo(..., resolved=resolved)` (line 88) — the
resolver isn't re-hit. Review-2 didn't catch the fix because it predates this session; the
hardening item is genuinely shipped. Worth a sentence in `memory.md` to that effect.

**Branch idempotency on apply is partially fixed too.** `executor/service.py:99-104`
catches `HTTPStatusError` with 422 from `create_branch` and reuses the existing ref. That
isn't the *full* fix (no detection of an already-open PR, no resumability of half-written
branches, no detection of human edits on the PR), but the prior review's "constant per-campaign
branch + 422-on-exists makes retries fail" is no longer accurate.

**The fetch-concurrency limit (default 8, `config.py:38`) is reasonable for GitHub's primary
rate-limit math.** At 8 in-flight requests averaging ~150ms, that's ~53 req/s. The
5000/hour/installation primary limit = 1.39 req/s sustained, so 8 concurrent without ETags will
burn the budget in ~30 minutes of continuous sweep, but the crons are spaced (5 min reconcile,
6 hr audit, 6 hr drift) so under steady state they fit. The actual risk isn't "too fast" —
it's "no 304s on the steady state, so you pay full price for unchanged files every sweep,"
and "no secondary-rate-limit handling so a burst on apply gets you blacklisted." The first
is wasted budget; the second is correctness.

### 1.3 Confirmed open

| Item | File:line | Verdict |
|---|---|---|
| ETags / `If-None-Match` / 304 handling | `github/client.py` (whole module) | Absent. No header reads, no storage. |
| Secondary-rate-limit / `Retry-After` backoff | `github/client.py:49,60,76,90,100,109,119,143,154` | Absent. Every call is bare `raise_for_status`. |
| Pagination on `list_workflow_runs` | `github/client.py:40-50` | Single page of 50. |
| Pagination on `list_workflow_files` | `github/client.py:52-65` | Returns the first page of `/contents/.github/workflows`. The contents API returns up to 1000 entries — fine for `.github/workflows/` in practice, but unverified for monorepos. |
| `X-GitHub-Delivery` dedup | `ingestor/app.py:38-62` | Header not read, no `processed_deliveries` table. |
| Webhook body size cap | `ingestor/app.py:45` | Unbounded `request.body()` before signature check. |
| `json.loads` guard | `ingestor/app.py:60` | Bare; 500 on malformed signed body. |
| SSE disconnect handling | `api/app.py:260-268`, `events/bus.py:51-64` | No `request.is_disconnected()`, no subscriber cap. Per-subscriber connection. |
| Pin classifier "unknown ref → TAG" | `audit/pins.py:85-87`, `providers/gitlab/audit.py:37` | Confirmed open — documented as by-design but still under-reports `@release`/`@stable`/`@feature-x`. |
| Token-cache race under `bounded_gather` | `github/factory.py:33-46` | New finding (see §1.1). Not in either prior review. |
| Live validation against real PG/Redis/GitHub | everything | Never done. Highest-priority unknown. |
| React build | `frontend/` | Never `npm install`/built (sandbox limitation). |
| OTel wiring | declared in deps; zero usage in `src/` | Confirmed via grep — `opentelemetry` strings appear only in `pyproject.toml`. |

---

## 2. Research findings (tied back to the code)

### 2.1 GitHub rate-limit and API correctness — the highest-leverage functional gap

**The numbers.** Primary REST limit is 5000 req/hr per installation (GitHub App), 15000/hr for
GitHub Enterprise Cloud orgs. Conditional requests with a 304 response **do not count** against
the primary limit when properly authorized ([docs](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api),
[endorlabs](https://www.endorlabs.com/learn/how-to-get-the-most-out-of-github-api-rate-limits)).
Most endpoints return `ETag` (and many also `Last-Modified`). For ActionsPlane this is the
free lunch — the steady-state sweep workload is "list 50 runs per repo, fetch N workflow files
per repo" where the answer is identical 95%+ of the time.

**Secondary limits** are separate and stricter: GitHub explicitly tells you to "wait at least
one second between POST/PATCH/PUT/DELETE requests" and limits "concurrent requests" (the famous
~100 concurrent / 900 points-per-minute soft limit). Returns 403 or 429 with a `Retry-After`
header. This is what bites a bulk-PR campaign: `apply_campaign` iterates targets in a single
serial loop today (good, accidentally), but the moment that's parallelized — or just slightly
unlucky on a big org — every `create_branch` + `put_file` + `create_pull_request` triple is
~3 POSTs per repo, hammering the secondary limit.

**Installation-token scoping interaction.** Each installation has its own 5000/hr bucket
(advantage: scales linearly with installs). But the *App-level* JWT mint endpoint has its own
limits; the token-cache race in §1.1 is worse than it looks because every duplicated mint
spends App-JWT budget too.

**The concrete fix shape for `github/client.py`:**
- Wrap every request in a helper that (a) injects `If-None-Match` if we have a cached ETag for
  that URL+method, (b) on `304 Not Modified` returns the cached body without counting against
  the limit, (c) on 403/429 inspects `Retry-After` and `X-RateLimit-*` and either sleeps with
  jitter or surfaces a typed `RateLimitedError` the worker can decide to defer on. Store the
  ETag cache in Redis with the URL hash as key — naturally per-installation because the
  Authorization header isn't in the key.
- Add a `paginate(url, ...)` helper that follows the `Link: rel="next"` header with a bounded
  cap. Use it for `list_workflow_runs` (the reconcile path) and consider it optional for
  `list_workflow_files` (which is small in practice).
- For the campaign apply path specifically: insert a 1.0-1.5s delay between repos on apply
  *by design*, not as a workaround — it matches GitHub's explicit guidance and prevents the
  secondary-limit cliff.

Sources: [GitHub Docs — Best practices for the REST API](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api);
[GitHub Docs — Rate limits for the REST API](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api);
[Endor Labs — How to Get the Most out of GitHub API Rate Limits](https://www.endorlabs.com/learn/how-to-get-the-most-out-of-github-api-rate-limits);
[github-conditional-http-transport](https://github.com/bored-engineer/github-conditional-http-transport).

### 2.2 Webhook delivery guarantees

GitHub webhooks are **at-least-once with retries on non-2xx within ~30s and on delivery
timeout**. Every delivery carries a unique `X-GitHub-Delivery` UUID. The correct dedup pattern
is documented unanimously across the practitioner literature:

1. Read the header.
2. Inside a transaction (or via a unique-constraint insert), check-and-insert the delivery ID
   into a `processed_deliveries(delivery_id PRIMARY KEY, received_at)` table.
3. If it was already there, return 200 immediately and **do not enqueue**.
4. TTL the table to "longer than GitHub's retry window" (24-48h is the common cushion).

This is missing in `ingestor/app.py:38-62`. The prior reviews flagged it; the architectural
note in `docs/ARCHITECTURE.md §9` ("Idempotent upserts. Webhook delivery is at-least-once,
and the reconciliation poller re-ingests recent runs, so the same row is written repeatedly
by design") is *half right*: the **persistence** is idempotent, but the **side effects** are
not. Specifically, every duplicate delivery today:
- Re-enqueues an arq job → re-runs the worker → re-`publish()`es a Redis envelope → every
  connected SSE subscriber sees a duplicate "update" event. Harmless individually, noisy at
  scale.
- For `push` events touching `.github/workflows/**`, re-enqueues `audit_repo_task` →
  re-fetches every workflow file → wastes GitHub-API budget on duplicate work.

The `processed_deliveries` table is ~30 lines of code in `db/models.py` + a one-line check in
`ingestor/app.py`. It's the highest-impact-per-line fix in the entire backlog.

**Ordering.** GitHub does *not* guarantee in-order delivery within an event type, and arq
is FIFO-by-default but with parallel workers loses ordering anyway. The idempotent-upsert
pattern handles out-of-order events for the *same* run because `WorkflowRun.id` is the GitHub
run id and later updates overwrite earlier — but only if you trust that "later upsert wins"
gives you "newest state." If a `completed` event arrives before an `in_progress` event for the
same run, the upsert will overwrite the completed state with in-progress. **This is a real
correctness bug** in `process_event` (`sync/worker.py:37-62`) — it's not been triggered yet
because there's no live traffic. The fix is to gate the upsert on `started_at`/`updated_at`
(only update if the incoming event is newer than the stored row), which is the standard
event-sourcing late-arrival handling. Worth catching in the cassette tests below.

**Backpressure into arq.** No DLQ, no `max_tries`, no poison-message handling. If a payload
breaks `process_event` (it can — there's no try/except around the handlers), arq retries with
backoff and eventually moves it… nowhere. There's no `_retry`/`dead` queue surfaced. For a
self-hosted production deploy this is a missing operability piece.

Sources: [GitHub Docs — Best practices for using webhooks](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks);
[Hookdeck — Implement Webhook Idempotency](https://hookdeck.com/webhooks/guides/implement-webhook-idempotency);
[Hookdeck — Webhook Delivery Guarantees](https://hookdeck.com/webhooks/guides/webhook-delivery-guarantees);
[community discussion #175725](https://github.com/orgs/community/discussions/175725).

### 2.3 SARIF find→fix bridge (deep dive — the strongest portfolio hook)

**What SARIF actually is in the GitHub ecosystem.** SARIF 2.1.0 JSON, uploaded either via
`github/codeql-action/upload-sarif` (a workflow step) or directly via `POST
/repos/{owner}/{repo}/code-scanning/sarifs`. GitHub renders results in the Security tab,
deduplicates by `partialFingerprints`, and supports `category` to distinguish multiple analyses
of the same commit (different tools, different sub-trees of a monorepo). The 2025-07-22
change made this stricter: SARIF files with multiple runs sharing the same `(tool, category)`
are now **rejected** — you get one analysis per `(tool, category)` per commit. That maps cleanly
onto a per-repo audit upload.

**The find→fix loop, end-to-end:**
1. **Find.** Existing OSS linters emit SARIF: zizmor (`--format=sarif`), octoscan, Scorecard,
   OpenSSF/scorecard-action.
2. **Ingest.** Read the SARIF JSON, map each `result` onto our `Finding` model. The mapping is
   trivial — SARIF has `ruleId` (→ `finding_type`), `level` (→ `severity`), `message.text` (→
   `message`), `locations[].physicalLocation.artifactLocation.uri` + `region.startLine` (→
   `path` + a new line attribute). `partialFingerprints` is a *natural* match for our existing
   `fingerprint` column — we already sha256 over `(repo:path:type:ref)`; SARIF
   `partialFingerprints` is a dict of named fingerprints so we'd add ours as one entry.
3. **Bridge.** When the finding's `ruleId` belongs to a class we can fix
   (`unpinned_action`, `template-injection`, `excessive-permissions`), enqueue a campaign that
   targets the repo+path and opens a PR. The PR body cross-references the SARIF alert by id.
4. **Emit.** **And here is the real differentiation.** ActionsPlane's *own* audit findings
   should be emitted **as SARIF** and uploaded to each repo's Code Scanning. So the same
   finding shows up in GitHub's Security tab next to zizmor's, with a "fix me" link to a
   ActionsPlane PR. This is the "evidence" half of the loop and what closes it.

**Why this is more interesting than just "ingest SARIF."** Two reasons. First, *emitting*
SARIF means ActionsPlane participates in GitHub's native security UI without trying to
replace it — orgs already trained on the Security tab don't have to learn a new dashboard.
Second, the **same** SARIF artifact becomes the compliance-evidence export (CISA SSDF /
EO 14028) that `feature-research.md §13 #13` calls for. One artifact, two consumers. That's the
shape of a Staff-level design choice.

**Implementation specifics for ActionsPlane:**
- New module `actionsplane/sarif/` with `to_sarif(findings: list[Finding]) -> dict` and
  `from_sarif(sarif: dict) -> list[Finding]`. Pure, side-effect-free, unit-testable.
- New GitHub-client method `upload_sarif(owner, repo, *, sarif: dict, commit_sha: str, ref: str)`
  posting to `/repos/{owner}/{repo}/code-scanning/sarifs` (gzip+base64). Needs
  `security_events: write` permission on the App.
- Build `partialFingerprints` deterministically from `(repo_id, path, finding_type, ref,
  line_no)` — same shape as our existing `fingerprint` column so dedup composes.
- For the *ingest* direction, a CLI command `actionsplane sarif import <file>` and an arq job
  triggered by an `actions.workflow_run` webhook whose `name` matches a configured list (e.g.
  "zizmor", "scorecard"), downloading the run's SARIF artifact and ingesting.
- The fix campaigns: introduce `OPERATIONS["fix-template-injection"]` (rewrite `${{ ... }}` in
  `run:` to `${VAR}` + `env:`) and `OPERATIONS["set-permissions"]` (least-privilege block).
  Both ruamel round-trip. Plus the existing `pin-shas`. Three ops cover the top three SARIF
  rule classes by frequency.

**The 2026 framing.** GitHub's lock-file work moves the *pinning* fix into the platform.
SARIF find→fix is robust against that — pinning was always the lowest-hanging finding; the
high-value ones (template injection, broad permissions, dangerous checkout, `pull_request_target`
misuse) are exactly the ones SARIF tools like zizmor focus on and **GitHub is not natively
fixing.** This is where ActionsPlane plants its flag without being undercut by the platform.

Sources: [GitHub Docs — SARIF support for code scanning](https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/sarif-support-for-code-scanning);
[GitHub Docs — Uploading a SARIF file to GitHub](https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/uploading-a-sarif-file-to-github);
[GitHub Changelog 2025-07-21 — code scanning will stop combining multiple SARIF runs](https://github.blog/changelog/2025-07-21-code-scanning-will-stop-combining-multiple-sarif-runs-uploaded-in-the-same-sarif-file/);
[REST API — code scanning](https://docs.github.com/en/rest/code-scanning/code-scanning);
[zizmor — Integrations docs](https://docs.zizmor.sh/integrations/);
[zizmor-action](https://github.com/zizmorcore/zizmor-action).

### 2.4 The "GitHub-2026 evidence plane" repositioning — validation / pushback

The thesis in `directions-research.md §(d) bet 1`: GitHub is shipping the primitives (lock files,
scoped secrets, egress firewall, Data Stream); ActionsPlane becomes the **fleet adopter and
evidence layer** on top. The 2026-03-26 [GitHub blog post](https://github.blog/news-insights/product-news/whats-coming-to-our-github-actions-2026-security-roadmap/)
is the source of truth here. Pushback in three layers.

**Where the thesis is real.** GitHub is genuinely not shipping cross-org rollout. The
roadmap items are *per-repo capabilities you have to opt into*: a workflow author has to write
the `dependencies:` block, an admin has to enable scoped secrets, an admin has to configure
an egress allowlist. None of that comes with "do it for 400 repos" tooling. ActionsPlane's
campaign engine is exactly that "do it for 400 repos" tooling, and its all-edits-via-PR thesis is
the right safety posture for rollout-as-migration. The 3-9 month preview→GA window means
ActionsPlane has an asymmetric opportunity to be the first orchestrator that *speaks the new
primitives natively*.

**Where the thesis weakens.** The Actions Data Stream + native egress firewall partially
subsume what would otherwise be ActionsPlane's most-distinct telemetry/audit work. Specifically:
- The Data Stream gives near-real-time telemetry with dependency-resolution and (later)
  process/file/network events — to S3 or Event Hub. That is **a much richer ingest source than
  webhooks**, and the docstring story in `docs/ARCHITECTURE.md §3` ("webhooks first, polling as
  fallback") will look dated within 6-9 months. *Self-hosted-ARC fleets* don't get the Data
  Stream though, so the webhook+polling path stays relevant for that slice.
- The egress firewall does runtime control natively for GitHub-hosted runners. That obsoletes
  the harden-runner-style audit posture work on the GH-hosted side. Self-hosted/ARC still needs
  it; emphasize that.

**Where the thesis is wishful.** Three caveats.

1. **"Compliance evidence export" as a differentiator** is weaker than it reads. CISA SSDF /
   EO 14028 self-attestation forms are *narrative-driven*: a CISO signs a PDF. They are not
   "upload your tool's JSON." The opportunity is real (mapping technical posture → narrative
   answers), but framing it as "export the artifact" misreads what compliance teams actually
   need. The credible play is **a per-control evidence binder** (per CISA control, here are
   the repos that pass and the ones that don't, here are the open findings and their
   remediation PRs). That's a UI/report shape, not a JSON export.

2. **The MCP angle (bet 2)** is plausible but is a *marketing* differentiator more than a
   *technical* one. An MCP server over the existing read API is ~200 lines; the actual
   technical depth is the same as the existing read API. Don't lead with MCP — lead with the
   read API being good, and add MCP as a one-week followup.

3. **"Adopt and stay converged on those primitives with a GitOps trail" is the headline
   sentence.** But the *evidence trail* is the closed PR + the merge commit + the SARIF that
   gets re-uploaded showing the finding closed. That trail already exists in GitHub. Don't
   re-build it; *aggregate and report on it.* The pitch is "we make 400 repos look like one
   repo from a posture-and-evidence perspective." That's tighter than "evidence plane."

**Net.** The repositioning is real and defensible *if* it's anchored on the rollout/migration
ability (campaigns + dry-run + PR) and the cross-repo posture aggregation. It is *not*
defensible if positioned primarily as "we ingest the Data Stream" (Splunk/Datadog do that
already with less effort) or "we export compliance evidence" (misreads the buyer). Lead with
*migration*, not telemetry.

Sources: [GitHub Blog — Actions 2026 security roadmap](https://github.blog/news-insights/product-news/whats-coming-to-our-github-actions-2026-security-roadmap/);
[community discussion #190621](https://github.com/orgs/community/discussions/190621);
[DEV — Complete Guide to GitHub Actions 2026 Security Roadmap](https://dev.to/x4nent/complete-guide-to-github-actions-2026-security-roadmap-dependency-locking-native-egress-5aap);
[Tenki Blog — GitHub Actions Workflow Lockfiles Are Coming](https://www.tenki.cloud/blog/github-actions-workflow-lockfiles).

### 2.5 Drift modeling rigor

The current `drift/engine.py` compares ASTs at three granularities (jobs present/absent,
step-sequence by `uses:` ref prefix, content values). That's a respectable v1 but it has
some hardcoded shortcuts worth naming:

- `_step_signatures` (`engine.py:39-47`) compresses every `run:` step to the literal string
  `"run:"`. Two completely different shell scripts compare equal as steps. That deflates
  drift signal — a canonical template with `run: ./bin/build.sh` and a candidate with
  `run: rm -rf $HOME` look identical at the structural level. Fix: hash the `run:` body
  (sha1 of stripped) so different scripts produce different signatures, with a separate
  "content drift" bump.
- `with:` parameters aren't compared at all. A canonical `uses: actions/checkout@v4 with:
  fetch-depth: 0` vs candidate `with: fetch-depth: 1` is reported as identical. Real drift.
- YAML canonicalization edge cases not handled: anchors/aliases (planned exclusion per plan
  §10), `on:` vs `True` quirk (handled in the parser), but also key-ordering inside `with:`
  blocks (cosmetic but not flagged minor), and matrix expansion (one canonical job → N candidate
  jobs is `STRUCTURAL_DRIFT` today; correct, but the right answer is to compare the matrix
  *spec* not the expanded list).
- The "name-only change is cosmetic" minor bump (`engine.py:93-96`) is over-eager: workflow
  display name *is* a string the org cares about. Treat the name-only case as `MINOR` for
  external display, but don't suppress it in dashboards.

The semantic-equivalence direction (worth taking on for v2): canonicalize before diffing.
Strip comments, sort `with:` keys, normalize `on:` shorthand (`on: push` → `on: {push: {}}`),
expand `matrix` only if both sides match. That's a real engineering chunk but it pays for
itself in false-positive reduction.

### 2.6 Prior art / competitive gap

`feature-research.md (a)` already maps this well; one sharpening. The market has shifted in
two ways since that doc was written.

**Zizmor closed half the find→fix gap.** Autofix is now in zizmor for the common rule
classes ([release notes](https://docs.zizmor.sh/release-notes/)). The killer is that zizmor's
autofix is *local file-in-place* — no PR, no org rollout, no dry-run state, no review gate.
ActionsPlane reframes as "the campaign engine that drives zizmor's own autofixes across N
repos as reviewable PRs," which is materially different from "we ingest zizmor SARIF and
fix it." Subtle but important: don't reinvent zizmor's fix rules, *invoke* them. New module:
`executor/external_fixers.py` that runs `zizmor --fix=all` against a fetched workflow text and
captures the diff. Then the existing campaign engine handles dry-run + PR.

**StepSecurity is moving toward platform.** Their harden-runner is GitHub's native egress
firewall's competitor; the SaaS dashboard is what they're selling. Self-hosted OSS for an org
that doesn't want a SOC subscription is still ActionsPlane's lane, but the "OSS alternative to
StepSecurity" framing weakens as GitHub natively ships the egress control. Reposition: not
"OSS StepSecurity" but "the GitOps layer that sits *above* whoever's doing runtime control,
hosting-side or self-hosted."

**Nothing in prior art does multi-CI orchestration with shared finding model.** The GitLab
parser + audit, though tactical, hints at the real moat — a Provider Protocol that lets you
ask "across both my GitHub and my GitLab fleet, which jobs are unpinned/over-permed?" Nobody
in the OSS or commercial space does this with a unified model today. **This is the most
defensible "moat" item in the whole project**, but only if the GitLab side completes the
triangle (observe + audit + edit). One-pillar GitLab support is worse than no GitLab support
because it cheapens the framing.

---

## 3. Prioritized recommendation table

Sorted by impact / effort. "Impact" is *correctness/security/portfolio-signal*, weighted
roughly equally. "Effort" is in person-days.

| # | Item | Impact | Effort | File(s) / Module | Rationale |
|---|---|---|---|---|---|
| 1 | **Add `X-GitHub-Delivery` dedup table + check** | High (correctness) | 0.5d | `ingestor/app.py`, new migration `0006_processed_deliveries`, `db/models.py` | Eliminates a whole class of double-processing on at-least-once redelivery. Highest impact per line in the whole backlog. |
| 2 | **ETag / `If-None-Match` / 304 handling in `github/client.py`** | High (perf + scale) | 2d | `github/client.py`, new `github/etag_cache.py`, Redis key `etag:<sha1>` | Free polling for unchanged files — the steady-state sweep workload is 95% unchanged. Closes the most-cited open finding from both prior reviews. |
| 3 | **Secondary-rate-limit handling (`Retry-After` + 403/429 backoff) + per-installation `asyncio.Lock` in the token cache** | High (correctness) | 1.5d | `github/client.py` (wrap every request), `github/factory.py:33-46` | One rate-limited call currently sinks the whole gather. The token-cache race is a new finding — adds a per-install lock so we don't hit App-JWT mint duplication under concurrent sweeps. |
| 4 | **Emit ActionsPlane findings as SARIF + upload to Code Scanning** | High (portfolio + functional) | 3d | new `actionsplane/sarif/` module, new `github/client.upload_sarif`, App scope add | The single biggest portfolio differentiator that no incumbent does. Closes the find→fix loop in *both* directions; doubles as compliance-evidence artifact. Lead the README with this. |
| 5 | **Webhook body-size cap (in-app) + `json.loads` try/except + ordering guard on `process_event` upsert** | Med-High (correctness/DoS) | 0.5d | `ingestor/app.py:45,60`, `sync/worker.py:37-62` | Three small wins in one PR. The ordering guard catches the late-arrival-event bug (§2.2) that will fire silently on live traffic. |
| 6 | **`hypothesis` property tests for `audit/pins.py` round-trip + `executor/operations.py` ruamel round-trip** | Med-High (correctness + Staff signal) | 2d | `tests/test_pins_properties.py`, `tests/test_operations_properties.py` | This is the Staff-signal item the prior docs flagged. Specifically: property "ruamel rewrite touches only `uses:` lines and re-parses to the same AST modulo the intended change" — exactly where subtle YAML bugs hide. Hypothesis is the right tool. |
| 7 | **Recorded HTTP cassettes for `github/client.py` write paths** | Med-High (correctness + Staff signal) | 1.5d | `tests/cassettes/`, swap `pytest-httpx` for `vcrpy` or `pytest-recording` | The write paths (`create_branch`, `put_file`, `create_pull_request`) currently aren't tested against real GitHub-shaped responses (including 422-on-exists, 409-on-stale-sha, 404-on-missing-ref). A cassette corpus closes that gap without a live token. |
| 8 | **Wire OTel end-to-end across ingest → audit → campaign as one trace** | Med-High (operability + Staff signal) | 1d | `ingestor/app.py`, `sync/worker.py`, `audit/service.py`, `executor/campaigns.py` | Deps already in `pyproject.toml`; zero usage in `src/`. One delivery → one trace ID propagated through arq context is exactly the "I know how to dogfood OTel" demo. |
| 9 | **Fix pin classifier `@release`/`@stable` downgrade** | Med (security false-negative) | 1d (with API resolve) or 0.25d (heuristic) | `audit/pins.py:78-87`, `providers/gitlab/audit.py:31-37` | Carried-forward High from review-1. Cheapest fix: treat any non-SHA / non-strict-semver as `BRANCH_PINNED` (HIGH). Real fix: optional ref-resolution via GitHub API to distinguish "this is a known tag with N releases" from "this is a branch." Heuristic first; API-resolve as v2. |
| 10 | **`bounded_gather` cancellation + per-installation lock; SSE disconnect + subscriber cap** | Med (correctness/perf) | 1d | `sync/concurrency.py`, `events/bus.py:51-64`, `api/app.py:260-268` | Both are "works fine until it doesn't" hardening. SSE leak grows with reconnecting dashboards. Lock prevents JWT-mint duplication under cold cache. |
| 11 | **Composite index `(repo_id, last_seen_at)` and partial index on `audit_findings(severity)` for the `/findings` filter path** | Med (perf at scale) | 0.25d | migration `0006_findings_severity_idx`, after #1's migration | Migration 0005 indexes `(repo_id, resolved_at)` which serves `open_findings(repo_id=...)`. It does **not** serve `open_findings(severity='HIGH')` (the org-wide scorecard view) — that scan still hits every row. A partial index `WHERE resolved_at IS NULL` on `(severity, last_seen_at)` covers the dashboard hot path. So no, migration 0005 isn't quite the right index for the dashboard. |
| 12 | **Live validation against real PG / Redis / a personal GitHub App / a minikube** | High (everything) | 1-2d | end-to-end | The biggest unknown unknown. Until this runs, every claim is provisional. Do this *before* portfolio submission; it's also the source of the cassette corpus for #7. |
| 13 | **`processed_deliveries` retention + arq DLQ + `audit_findings` retention policy** | Med (operability) | 1d | new cron in `sync/worker.py`, arq `max_tries`/`_retry` config, migration | Operationalize the storage layer. Time-partitioning `workflow_runs`/`workflow_jobs` by month (PG declarative partitions) deferred to a v2 — flag it now. |
| 14 | **Per-installation tenant-isolation guard at the repository layer (mandatory `installation_id` filter)** | Med (security) | 1d | `db/repository.py` (every query), tests | A cross-tenant leak path is a credibility-killer for a self-hosted multi-org tool. Postgres RLS is overkill; an explicit `installation_id` parameter on every query function is enough. |
| 15 | **Resolve `uses:` refs via the parsed AST in `executor/service.py:_resolve_pin_refs`** | Med (security/correctness) | 0.5d | `executor/service.py:30-47` | Carried forward from review-2. Line-scan currently can pick up `uses:` substrings inside `run:` heredocs. Use `wf.all_uses()` like the audit engine does — same source of truth as the rewriter. |
| 16 | **Validate `operation` against `OPERATIONS` registry at campaign-create time; validate `owner`/`repo` against `^[A-Za-z0-9._-]+$`** | Low-Med (defense in depth) | 0.5d | `api/app.py:214-230`, helper in `github/client.py` | Small, cheap, closes carried-forward review-2 findings. |
| 17 | **`processed_deliveries` index check vs. arq job ID** | Low | 0.25d | follow-up to #1 | Use the delivery ID as the arq `_job_id` so arq's own dedup catches the rare race between dedup-check-and-insert. Belt and suspenders. |

**Out of scope for v1 / explicit deferrals (with rationale):**
- **GitLab observe/edit pillars.** Don't ship two pillars on a second provider; ship three or
  none. Better to land SARIF emit on GitHub first.
- **PG partitioning of `workflow_runs`/`workflow_jobs`.** Real but not v1.
- **Workflow log secret-leak scan.** Native egress firewall + Data Stream reduce the urgency.
- **MCP server.** Two-week followup *after* SARIF emit lands. Don't lead with it.
- **CEL/Rego policy gate.** Demoted by GitHub's evaluate-mode in the 2026 roadmap.

---

## 4. Portfolio sequencing — sharp call

The question is which one of {SARIF find→fix, evidence-plane bet, finish GitLab} most makes this
read as Staff/Principal work.

**SARIF find→fix is the right next bet. Specifically: emit ActionsPlane findings as SARIF
uploaded to Code Scanning, with `pin-shas` (existing) + `fix-template-injection` + `set-permissions`
as the three campaign ops that close the loop.** Reasoning, ranked:

1. **It plugs ActionsPlane into a UI ten million developers already use.** The Security tab
   is where this finding gets seen. No new dashboard required. That is *exactly* the platform-
   engineering move ("compose with what exists; don't make people learn another tool") that
   reads as Staff/Principal.
2. **It's defensible against GitHub's lock-file work.** Pinning was always the easy finding;
   the hard ones (template injection, broad perms, `pull_request_target` patterns) are the ones
   neither GitHub nor zizmor-autofix touches at fleet scale.
3. **It is technically substantial without being a yak shave.** SARIF emit + upload + three
   campaign ops + the bridging code is ~3-5 days for someone who already wrote the engine.
   That's a demonstrable, complete, reviewable chunk.
4. **It naturally subsumes the evidence-plane bet.** The same SARIF artifacts ARE the
   compliance evidence; you don't need a separate JSON exporter. Bet 1 (evidence plane)
   becomes "yes, and it's already there in SARIF" instead of a third pillar.

**The evidence-plane bet is the right *framing*, not the right next implementation
target.** Use it in the README; lead the actual code work with SARIF emit. The roadmap
positioning matters for the portfolio narrative ("I read GitHub's 2026 roadmap and built the
gap"); the SARIF emit is the demoable proof.

**GitLab is a trap right now.** One-pillar GitLab support actively *weakens* the framing
because it forces a "but actually it only audits, doesn't observe or edit" footnote. Either
commit to full GitLab parity (4-6 weeks) or rip it back to a `providers/base.py` Protocol with
a stub note ("future"). I'd rip it back: the moat is GitHub depth + SARIF bridge + 2026
positioning, not provider count.

---

## 5. Top security and performance findings (consolidated, with file refs)

For each, file + line + the fix that should ship.

**S1 — `X-GitHub-Delivery` dedup absent.** `ingestor/app.py:38-62`. New
`processed_deliveries(delivery_id PRIMARY KEY, received_at)` table with TTL pruning; check
before `enqueue_event`. **Severity:** Med (correctness; visible at scale, invisible under
test fixtures).

**S2 — Token-cache race under `bounded_gather`.** `github/factory.py:33-46`. Add
`dict[int, asyncio.Lock]` keyed by `installation_id`; acquire before the check-and-mint.
**Severity:** Med (wastes JWT budget; can wedge under network flakiness; not in either prior
review).

**S3 — Ordering bug on late-arrival webhook upserts.** `sync/worker.py:37-62`. Currently
`upsert_run`/`upsert_job` unconditionally overwrites. If `completed` arrives before
`in_progress` for the same run id, the row regresses. Gate the upsert on `updated_at >
stored_updated_at`. **Severity:** Med (latent correctness bug; will fire on live traffic).

**S4 — No secondary-rate-limit handling.** `github/client.py` (every request method). Wrap
in a helper that inspects `Retry-After` on 403/429 and either sleeps with jitter or raises a
typed `RateLimitedError`. **Severity:** High (will trigger on first real campaign of any size).

**S5 — Webhook body-size cap missing.** `ingestor/app.py:45`. Enforce
`Content-Length <= 25MB` (configurable) before `await request.body()`. **Severity:** Low
(unauthenticated DoS surface; mitigated by reverse-proxy in practice).

**P1 — No ETags / 304s.** `github/client.py` whole module. Already detailed above.
**Severity:** High (defines whether the org-wide cron is sustainable).

**P2 — `audit_findings` org-wide severity scan is unindexed.** `db/repository.py:103-120`,
migration `0005`. The current index `(repo_id, resolved_at)` does not serve
`open_findings(severity='HIGH')` for the org-wide scorecard call (no `repo_id`). Add a partial
index `ON audit_findings(severity, last_seen_at) WHERE resolved_at IS NULL`. **Severity:**
Med (only bites at scale).

**P3 — SSE per-subscriber Redis connection without cap or disconnect check.**
`events/bus.py:51-64`, `api/app.py:260-268`. Add `request.is_disconnected()` polling +
configurable subscriber cap. **Severity:** Med (leaks under reconnecting dashboards).

**P4 — Pin classifier `@release`/`@stable` false negative.** `audit/pins.py:78-87`,
`providers/gitlab/audit.py:31-37`. Either resolve via API or treat non-strict-semver as HIGH.
**Severity:** Med (under-reports the exact attack the module exists to catch).

---

## 6. Sources

GitHub API correctness:
- [GitHub Docs — Best practices for using the REST API](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)
- [GitHub Docs — Rate limits for the REST API](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
- [GitHub Docs — REST API endpoints for rate limits](https://docs.github.com/en/rest/rate-limit/rate-limit)
- [Endor Labs — How to Get the Most out of GitHub API Rate Limits](https://www.endorlabs.com/learn/how-to-get-the-most-out-of-github-api-rate-limits)
- [bored-engineer/github-conditional-http-transport (Go reference impl)](https://github.com/bored-engineer/github-conditional-http-transport)
- [community discussion #189255 — Working with the GitHub API rate limit](https://github.com/orgs/community/discussions/189255)
- [community discussion #156480 — handling rate limits for frequent polling](https://github.com/orgs/community/discussions/156480)

Webhook delivery guarantees:
- [GitHub Docs — Best practices for using webhooks](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)
- [Hookdeck — Implement Webhook Idempotency](https://hookdeck.com/webhooks/guides/implement-webhook-idempotency)
- [Hookdeck — Webhook Delivery Guarantees](https://hookdeck.com/webhooks/guides/webhook-delivery-guarantees)
- [community discussion #175725 — efficient retry handling + dedup](https://github.com/orgs/community/discussions/175725)
- [community discussion #151676 — handling webhook retries](https://github.com/orgs/community/discussions/151676)
- [Webhook Reliability 2026 reference](https://www.digitalapplied.com/blog/webhook-reliability-idempotency-retries-engineering-reference-2026)

SARIF find→fix bridge:
- [GitHub Docs — SARIF support for code scanning](https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/sarif-support-for-code-scanning)
- [GitHub Docs — Uploading a SARIF file to GitHub](https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/uploading-a-sarif-file-to-github)
- [GitHub Changelog 2025-07-21 — multi-run SARIF rejected](https://github.blog/changelog/2025-07-21-code-scanning-will-stop-combining-multiple-sarif-runs-uploaded-in-the-same-sarif-file/)
- [GitHub Docs — REST API endpoints for code scanning](https://docs.github.com/en/rest/code-scanning/code-scanning)
- [zizmor — Integrations docs](https://docs.zizmor.sh/integrations/)
- [zizmor — Release notes (autofix coverage)](https://docs.zizmor.sh/release-notes/)
- [zizmor-action](https://github.com/zizmorcore/zizmor-action)

GitHub 2026 roadmap (evidence-plane bet):
- [GitHub Blog — Actions 2026 security roadmap (2026-03-26)](https://github.blog/news-insights/product-news/whats-coming-to-our-github-actions-2026-security-roadmap/)
- [community discussion #190621 — roadmap Q&A](https://github.com/orgs/community/discussions/190621)
- [DEV — Complete Guide to the 2026 Security Roadmap](https://dev.to/x4nent/complete-guide-to-github-actions-2026-security-roadmap-dependency-locking-native-egress-5aap)
- [Tenki Blog — Workflow Lockfiles Are Coming](https://www.tenki.cloud/blog/github-actions-workflow-lockfiles)
- [Wiz — Hardening GitHub Actions: Lessons from Recent Attacks](https://www.wiz.io/blog/github-actions-security-guide)

Prior internal review docs (used to avoid re-litigating settled items):
- `docs/memory.md`
- `docs/review-findings.md`
- `docs/review-findings-2.md`
- `docs/feature-research.md`
- `docs/directions-research.md`
- `docs/multi-ci-research.md`
