# ActionsPlane — Resume Chat

> **For continuing this conversation from VS Code (or any new chat session).**
> Paste section §1 below as the first prompt; everything else is reference Claude can read.

---

## §1. First prompt to send (copy-paste this)

```
You're continuing work on ActionsPlane — a self-hosted, OSS control plane for GitHub
Actions across many repos (observe + audit + drift + edit, all edits via PRs).

Before you do anything else, read these files IN ORDER:

  1. docs/memory.md              ← technical state-of-truth + sandbox gotchas. Read first.
  2. docs/resume-chat.md         ← this file (decisions, open questions, next moves)
  3. docs/staff-review.md        ← latest review; the v1.1 backlog comes from here
  4. plan.md                     ← design + §13 research additions
  5. docs/ARCHITECTURE.md        ← runtime view; §9–§12 capture per-phase decisions

Then confirm the test suite is green. Two environments:

  # Windows / VS Code (current — no shim needed, uv pulls real CPython 3.12):
  uv venv --python 3.12
  uv pip install -e ".[dev]" aiosqlite
  PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q     # expect 129 passing

  # Older Cowork sandbox (Python 3.10) — needs the shim from memory.md §6:
  mkdir -p /tmp/shim && cat > /tmp/shim/sitecustomize.py <<'EOF'
  import enum
  if not hasattr(enum,"StrEnum"):
      class StrEnum(str,enum.Enum):
          def __str__(self): return str(self.value)
      enum.StrEnum=StrEnum
  import datetime as _dt
  if not hasattr(_dt,"UTC"): _dt.UTC=_dt.timezone.utc
  EOF
  export PYTHONPATH="/tmp/shim:src"
  python -m pytest -q              # expect 129 passing

State as of last session: v1.1 in progress. The next concrete chunk is in §4 of this doc
("Next concrete actions"). Pick the top item unless I tell you otherwise.
```

---

## §2. Project in one breath

Self-hosted OSS control plane: a GitHub App that ingests `workflow_run`/`workflow_job` webhooks,
runs an audit engine (pin / least-privilege / deprecation / publisher trust) and a drift engine
(structural AST diff vs templates) across an entire org, then opens **bulk PRs** to fix what it
finds — never writing to `main` directly. Python 3.12 + FastAPI + arq + Postgres/Redis, React UI,
shipped with Docker, kustomize, and a Helm chart. The headline differentiator is the **SARIF
find→fix bridge** — emitting findings to GitHub Code Scanning so they land in the Security tab
alongside zizmor/CodeQL — which was wired in v1.1.

**Phases:** 1 Observe ✅ · 2 Audit ✅ · 3 Drift ✅ · 4 Edit ✅ functional · v1.1 hardening in
progress · GitLab provider deferred to v2 (one-pillar port today: parser + audit only).

**129 tests green, ruff-clean, 9 Alembic migrations parse (single head `0009_workflow_relations`). React UI refactored + runs live in docker-compose.full.yml on :3001 (Runs/Security/Drift/Pipelines); GitHub deep links + run re-run; `audit local` CLI; OFFLINE MODE (public-repo pull, no App). PIPELINES is a layered left→right flow graph (repo-coloured cards + curved typed connectors) where each node shows its latest-run status and, on failure, the failing job/step (e.g. Deploy → terraform apply); the run drawer shows a per-job step tree. Session 5 shipped the v1.1 hardening backlog: pagination, SARIF orchestration end-to-end (gated by `security_events_enabled`), disconnect-safe SSE, OpenTelemetry tracing (one trace ingest→worker→audit→SARIF, off by default), and hypothesis property tests. Session 6 added pipeline/run status + step trees (no migration — steps come from the job `raw_payload`). All re-validated on the live docker stack.**

---

## §3. Decisions made in earlier sessions (don't relitigate)

- **Two YAML representations** — Pydantic AST for *analysis* (audit/drift); ruamel round-trip
  for *edits* (preserves comments/format → reviewable PR diffs).
- **All edits via PR**, dry-run → human-approved apply; `bulk_edits_enabled` AND
  `ACTIONSPLANE_API_TOKEN` both required (fail-closed). Apply reuses the dry-run-resolved SHA
  map from `campaign.params`, immune to tag retargeting between preview and apply.
- **Idempotent everything** — GitHub-id upserts for runs/jobs; fingerprint
  (sha256 of repo:path:type:ref) upsert for findings + resolve-stale lifecycle.
- **Bounded concurrency** for sweeps (`sync/concurrency.py:bounded_gather` + per-task DB
  sessions + `ACTIONSPLANE_FETCH_CONCURRENCY`).
- **Per-installation `asyncio.Lock`** on the token cache (`github/factory.py`) — collapses the
  thundering-herd mint problem the staff review found.
- **Pure core, thin I/O.** audit/drift/metrics/operations/sarif are side-effect-free; services
  do the network and DB. This is what keeps the test count meaningful.
- **GitLab v2, not v1.** The parser + include/component pin audit is in for *Provider seam
  validation*, but observe/edit pillars are deferred — one-pillar provider support weakens
  the framing.
- **SARIF emit is the differentiator, not the "evidence plane" rebrand.** Per staff review:
  ship into the Security tab; the evidence-plane bet is naturally subsumed by the same artifact.
- **`security_events: write` scope is accepted** for SARIF upload (narrow GitHub permission;
  only writes to Code Scanning).
- **Lint:** ruff (E,F,I,UP,B,SIM,ASYNC,RUF), line-length 100. Per-file-ignores for B008 on
  cli/api/ingestor (typer/FastAPI defaults idiom), migrations relax E501/F401/E402.

---

## §4. Next concrete actions (in priority order)

These come from `docs/staff-review.md` and `docs/memory.md §4` — pick the top item unless the
user redirects.

1. ~~Latent ordering bug in `sync/worker.py`~~ — ✅ **DONE (session 4).** Not the "one-line
   WHERE fix" this doc predicted: `workflow_runs` had no `updated_at` column to gate on, so it
   needed a new column + migration `0008` + a normalizer change. `upsert_run` is now a
   conditional upsert (`... WHERE updated_at IS NULL OR updated_at <= excluded.updated_at`) and
   is dialect-portable, which bought the project's first DB-backed test
   (`tests/test_repository_run_ordering.py`). The **job** upsert has the same latent bug but no
   monotonic `updated_at` on the payload — deferred (needs a status-rank gate). **New top item
   is pagination, below.**
2. ~~**Pagination** in `github/client.py`~~ — ✅ **DONE (session 5).** Generic
   `_get_paginated(url, ...)` async-gen walks the RFC-5988 `rel="next"` Link header; `_get_cached`
   now keys the ETag cache by URL+params (fixes a latent page-collision bug) and returns headers.
   `list_workflow_runs` walks pages bounded by `max_runs` (truncation logged, not silent);
   `list_workflow_files` Link-walks too (no-op single page on the contents API). Two MockTransport
   tests (two-page concat + max_runs cap).
3. ~~**SARIF orchestration end-to-end.**~~ — ✅ **DONE (session 5).** `audit/sarif_service.py`:
   `upload_repo_sarif(session, gh, repo)` (injected client, sqlite+MockTransport tested) +
   `upload_sarif_for_repo(session, repo_id)` (mints client, gated). API `POST /repos/{id}/sarif/upload`
   (403/404/503/502 mapping). Worker `_maybe_upload_sarif` runs after every `audit_repo`. Gated by
   new `security_events_enabled` flag (default false). Empty result-sets upload too, so cleaned-up
   findings close their alerts.
4. ~~**SSE disconnect handling**~~ — ✅ **DONE (session 5).** `subscribe()` takes an injectable
   `conn` and only closes what it owns; the API `/events/stream` generator `aclose()`s the bus
   stream in a `finally`, checks `request.is_disconnected()`, and sets `ping=15` so idle channels
   still notice a dead client. Test drives the cleanup path with a fake pubsub.
5. ~~**`hypothesis` property tests**~~ — ✅ **DONE (session 5).** `tests/test_property.py`: classify
   never mis-ranks a SHA / never raises / idempotent on raw; `pin_workflow_to_sha` is idempotent,
   never drops comments, never emits un-parseable YAML, leaves no tag/branch refs.
6. **Live validation** — **PARTIALLY DONE (session 4).** Real-Postgres half is now validated
   locally: `make up && make migrate` against compose Postgres (8 migrations apply, head 0008),
   `scripts/seed_local.py` populates a demo read model, API serves it, and the ordering guard
   was re-confirmed on real PG. STILL pending (needs user's GitHub org): install the real App,
   smoke-test `ingest → audit → SARIF upload → campaign dry-run` end-to-end — that run produces
   the **HTTP cassette corpus** for write-path tests. See `docs/USER_GUIDE.md` for the runbook.
7. ~~wire **OTel** end-to-end~~ — ✅ **DONE (session 5).** `observability/tracing.py` (optional,
   import-safe; off unless `otel_enabled`). FastAPI instrumented on api+ingestor; `enqueue_event`
   injects the W3C carrier, `process_event` continues the trace; spans in `audit_repo` + `sarif`.
   One trace: ingest → worker.process_event → audit.audit_repo → sarif.upload. React app already
   built/running. **The §4 backlog is now empty except live-org validation (item 6).**

---

## §5. Where everything is

```
actionsplane/   (C:\Users\Itamar\Desktop\Claude\Actionsplane — standalone git repo)
├── plan.md                      design + §13 research additions
├── README.md
├── pyproject.toml · ruff.toml · mise.toml · Makefile · alembic.ini
├── scripts/seed_local.py        seed demo installation/repos/runs for local testing
├── docs/
│   ├── memory.md                ← technical handoff (always-current state)
│   ├── resume-chat.md           ← this file
│   ├── USER_GUIDE.md            run locally, seed, add repos via the GitHub App
│   ├── ARCHITECTURE.md          runtime view (§9 P1 notes, §10 P2/3, §11 P4, §12 k8s)
│   ├── feature-research.md      first product research (find→fix wedge)
│   ├── review-findings.md       first security/perf review
│   ├── review-findings-2.md     second review (apply path, rate-limit)
│   ├── directions-research.md   landscape research (GitHub 2026 primitives)
│   ├── multi-ci-research.md     GitLab vs Jenkins study
│   ├── staff-review.md          deep staff/principal review — v1.1 backlog source
│   └── k8s-architecture.md      Mermaid services-level diagram
├── src/actionsplane/
│   ├── config.py · models/ · ingestor/ · sync/ (concurrency + worker)
│   ├── github/ (app_auth, client, factory) · audit/ (parser, engine, pins,
│   │           findings, scorecard, service, sarif) · drift/ · metrics/
│   ├── executor/ (operations, service, campaigns) · events/ · api/ · providers/ · cli/
│   └── db/ (base, models, repository)
├── migrations/versions/
│   0001 initial · 0002 finding fp/path · 0003 templates · 0004 campaigns
│   0005 findings (repo) idx · 0006 processed_deliveries · 0007 findings partial (severity) idx
├── tests/                       129 tests
├── deploy/
│   ├── docker-compose.full.yml  one-command local sandbox
│   ├── docker/                  per-component Dockerfiles (api/ingestor/worker/frontend)
│   ├── k8s/                     kustomize manifests + README
│   └── helm/actionsplane/       Helm chart (Chart, values, templates, _helpers, NOTES)
└── frontend/                    Vite + React + TanStack Query
```

---

## §6. Open questions (only the still-pending ones)

1. **Live env timing** — user said "maybe this week." When ready, action item §4.6 runs.
2. **SARIF rollout pitch** — `security_events: write` was accepted; do we add an opt-in toggle
   `security_events_enabled` (default false) so the scope is only requested when the operator
   wants Code Scanning integration? My read: yes, mirror the `bulk_edits_enabled` pattern.
3. **GitLab observe/edit pillars** — deferred to v2 per staff review. If/when the user wants to
   resume, the Provider seam in `src/actionsplane/providers/base.py` is the entry point.

---

## §7. Sandbox gotchas (matters if you continue in this same sandbox)

- **Python 3.10**, project targets 3.12. The `StrEnum`/`datetime.UTC` shim in §1 backfills
  both. `/tmp` is cleared between sessions — **recreate the shim every time**.
- `pip install --break-system-packages` for any missing dep. Already installed: pydantic,
  pydantic-settings, fastapi, uvicorn, ruamel.yaml, typer, rich, pyjwt[crypto], sqlalchemy,
  httpx, arq, alembic, redis, sse-starlette, cryptography, pytest, pytest-asyncio, ruff,
  aiosqlite.
- No docker / kubectl / helm / npm; network restricted (uv can't fetch Python 3.12; npm
  registry blocked). Deployment YAML is validated structurally (parse + kustomize resource
  resolution + helm template brace/include checks) — first real `kubectl apply --dry-run=server`
  / `helm lint` / `docker build` lives on the user's machine.
- `plan.md` was read-only on copy from upload; `chmod u+w plan.md` then write works.
- Bash writes via the mounted `/sessions/.../actionsplane/` path are authoritative and reflect
  back to the user's Windows files. The mount sometimes lags on reads after a Windows-tool
  write — write via bash if you need to immediately re-read in the sandbox.

---

## §8. Tone the user expects

Senior DevOps/SRE, 8+ years. **They prefer a sharp disagreement over a safe summary.** Cite
file:line for any claim. Distinguish a verified vulnerability from a "not-yet-built gap."
Don't over-format with bullets when prose works. Be honest about what wasn't run (live env,
helm/kubectl/docker validation, frontend build).
