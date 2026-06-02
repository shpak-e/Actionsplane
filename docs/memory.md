# ActionsPlane — Working Memory / Handoff

> Resume doc. Read this first, then `docs/resume-chat.md` (conversational handoff + first-prompt to paste), `plan.md` (design), and `docs/ARCHITECTURE.md` (runtime).
> Last updated: 2026-06-01 (session 5). State: **v1.1 hardening backlog now CLEARED except live-org validation. Session 5 shipped: Link-header pagination (`max_runs`-bounded), SARIF orchestration end-to-end (`sarif_service` + `POST /repos/{id}/sarif/upload` + post-audit worker step, gated by `security_events_enabled`), disconnect-safe SSE (bus `aclose` in `finally` + `ping`), OpenTelemetry tracing (one trace ingest→worker→audit→SARIF, off by default + import-safe), hypothesis property tests, and the Pipelines tab as a layered left→right flow graph (repo-coloured cards + curved typed SVG connectors). Earlier (session 4): ordering guard, real-Postgres validation, React UI, offline mode, Pipelines, run re-run. Session 6: Pipelines nodes show latest-run status + failing job/step (`/pipelines` enriched via Workflow(repo_id,path)→latest run→jobs.steps); JobOut surfaces `steps` from the job `raw_payload` (no migration); run drawer renders a per-job step tree; seed now creates pipeline workflows + jobs/steps incl. a failing Deploy at `terraform apply`; also fixed the Pipelines empty-page infinite-render loop + score-ring centering.** 129 tests green, ruff-clean, 9 migrations. Re-validated on the live docker stack (api/worker/ingestor/frontend rebuilt; `/pipelines` statuses + step trees + SARIF gating + runs confirmed).

## 1. What this project is
Self-hosted OSS control plane for GitHub Actions across many repos: **observe + audit + edit**, all edits via PRs. Python 3.12 + FastAPI + arq + Postgres/Redis, React UI. The wedge: no OSS tool combines observe+audit+drift+bulk-PR-edit; the headline differentiator (not yet built) is the SARIF find→fix bridge.

## 2. Status by phase
- **Phase 1 Observe — ✅ done.** Webhook ingestor (HMAC, fail-closed) → arq queue → worker upserts (idempotent); GitHub App auth (JWT→installation token); reconcile cron; REST read API + live SSE (Redis pub/sub); pure metrics (success/p50/p95/queue/flake); React dashboard (Runs tab); Alembic schema.
- **Phase 2 Audit — ✅ done.** `audit/parser.py` (ruamel→Pydantic AST), `audit/engine.py` (pin, permissions incl. id-token/packages/actions, deprecation, publisher-trust, concurrency), `audit/service.py` (org-wide: fetch→parse→engine→persist), finding lifecycle (fingerprint upsert → resolve stale), posture scorecard, findings API, Security tab.
- **Phase 3 Drift — ✅ done.** `drift/engine.py` structural AST diff (identical→minor→content→structural), `workflow_templates`+`template_bindings`, filename autobind, drift_sweep cron, templates/bindings/drift API, Drift tab.
- **Phase 4 Edit — ✅ functional.** `executor/operations.py` pin-shas (ruamel round-trip, pure given resolver), `executor/service.py` (resolve→diff dry-run; branch→commit→PR), `executor/campaigns.py` (dry_run/apply orchestration, per-target status, apply gated by `bulk_edits_enabled`), GitHub write client, campaigns API + CLI.
- **GitLab provider — 🚧 started (v2).** `providers/base.py` Provider Protocol; `providers/gitlab/` parser (`.gitlab-ci.yml`→jobs+includes) + include/component pin audit (reuses Finding/PinState). Parser + audit done & tested; observe/edit pillars NOT ported yet.

## 3. Module map (`src/actionsplane/`)
- `config.py` — pydantic-settings (`ACTIONSPLANE_*`); `api_token`, `bulk_edits_enabled`, `github_app_*`.
- `models/` — workflow AST (`Workflow/Job/Step`) + `enums` (PinState, Severity, FindingType, DriftSeverity, CampaignStatus).
- `ingestor/` — `signature.py` (HMAC), `app.py` (webhook→enqueue), `events.py` (payload normalization, pure).
- `sync/` — `queue.py` (arq enqueue), `worker.py` (process_event, reconcile, audit_all, audit_repo_task, drift_sweep + crons).
- `github/` — `app_auth.py` (JWT/token), `client.py` (REST read+write: runs, files, commit-sha, branch, put_file, PR), `factory.py` (client_for_installation).
- `audit/` — `pins.py`, `parser.py`, `engine.py`, `findings.py` (+`fingerprint`), `deprecations.py`, `service.py`, `scorecard.py`, `sarif.py` (pure emit), `sarif_service.py` (orchestration: open findings → SARIF → upload; gated by `security_events_enabled`).
- `observability/` — `tracing.py` (OpenTelemetry; `setup_tracing`, `instrument_fastapi`, `inject_context`/`continue_trace` for cross-queue context, `span`). Optional + import-safe; no-op unless `otel_enabled`.
- `drift/` — `engine.py` (diff), `service.py` (compute_drift, autobind_paths).
- `executor/` — `operations.py` (pin_workflow_to_sha, unified_diff, OPERATIONS), `service.py` (dry_run_repo, open_pr_for_edits), `campaigns.py` (run_dry_run, apply_campaign), `actions.py` (rerun_run — re-run a workflow run on GitHub).
- `metrics/compute.py` — pure stats.
- `events/bus.py` — Redis pub/sub (publish singleton, subscribe for SSE).
- `api/` — `app.py` (all endpoints), `schemas.py`, `auth.py` (bearer-token gate on `/api/v1`).
- `providers/` — `base.py` (Protocol), `gitlab/` (parser, audit).
- `cli/main.py` — typer: `version`, `status`, `audit local|all|pins|perms`, `drift`, `campaign preview|create|status`.
- `offline/sync.py` — offline mode: `parse_repo_spec`, `sync_repo`, `sync_offline` (public-repo pull, no App).
- `relations/analyze.py` — Pipelines: `extract_relations` (per-workflow descriptor), `build_pipeline_graph` (fleet graph).
- `db/` — `base.py` (lazy async engine), `models.py` (ORM), `repository.py` (all query/upsert helpers).
- `migrations/versions/` — 0001 initial · 0002 finding fp/path · 0003 templates · 0004 campaigns · 0005 findings (repo) index · 0006 processed_deliveries · 0007 findings partial (severity) index · 0008 workflow_runs.updated_at (ordering guard) · 0009 workflow_relations (Pipelines).

## 4. What's LEFT (continuation backlog, priority order)
DONE in session 4 (from `docs/staff-review.md` S3 / resume §4.1):
- ✅ **Out-of-order webhook upsert guard** — `workflow_run` redeliveries are at-least-once and
  unordered, so a late `in_progress` could regress a stored `completed` row. Added a monotonic
  `updated_at` column to `workflow_runs` (migration 0008), captured in `normalize_run_object`,
  and made `upsert_run` a *conditional* upsert: `ON CONFLICT DO UPDATE ... WHERE updated_at IS
  NULL OR updated_at <= excluded.updated_at`. Guard stays in SQL (atomic; a read-then-write
  would reopen the race). Also shields reconcile (REST) from clobbering a fresher webhook row.
  The upsert is now **dialect-portable** (`pg_insert`/`sqlite_insert` dispatch) — which gave us
  the project's **first DB-backed behavioural test** (`tests/test_repository_run_ordering.py`,
  in-memory sqlite, JSONB→JSON compile shim). NOTE: resume §4.1 called this a "one-line WHERE
  fix" — it wasn't; there was no `updated_at` column to gate on, so it needed a new column +
  migration + the normalizer change.

- ✅ **First real-Postgres validation (local)** — brought up `deploy/docker-compose.yml`
  (PG+Redis) on the user's Windows/Docker box and ran `alembic upgrade head` against real
  Postgres: all 8 migrations apply, head `0008`, `workflow_runs.updated_at` present. Seeded via
  new `scripts/seed_local.py`, started the API, and confirmed `/api/v1/repos` + `/runs` serve
  the seeded read model. **Re-ran the ordering guard against real Postgres** (not just sqlite):
  a late `in_progress` with an older `updated_at` was rejected, `completed/success` survived.
  Still NOT run: the live GitHub ingest→audit→SARIF→PR loop (needs a real App + webhook tunnel)
  and `helm/kubectl/docker build` of the per-component images. This partially closes §4.1 below.
- ✅ **React UI built, refactored & running live** — installed Node 24 (winget); `npm run build`
  compiles clean (tsc + vite). Then a full UI/UX refactor: new design-system `styles.css` (tokens,
  elevation, focus states), app shell with brand header + segmented tabs + live SSE indicator,
  sidebar, redesigned run grid (status badges, branch/commit chips, relative time + duration),
  **run detail as a slide-over drawer** (replaces the cramped 3rd column) with facts, metrics,
  jobs, and actions; scorecard **gauge** + severity bar; loading skeletons / empty / error states;
  a11y (focus-visible, aria, Esc-to-close). New `lib/format.ts`, `lib/github.ts`, `hooks/useRepos`,
  `components/ui.tsx` (icons/badges/states), `RerunButton`. Runs in `docker-compose.full.yml`
  (new `frontend` service on :3000; api gets a `actionsplane-api` network alias so the existing
  nginx.conf resolves). Image build verified via `docker compose build frontend`.
- ✅ **Worker startup bug fixed (pre-existing, found by running the full stack)** — `WorkerSettings.redis_settings`
  was a `@staticmethod`, but the installed arq expects a `RedisSettings` *instance* attribute →
  `AttributeError: 'staticmethod' object has no attribute 'host'`, worker crash-looped (Exited 1).
  Never caught because the worker had never been started live. Now a `ClassVar[RedisSettings]`
  resolved from the env DSN at import. Worker comes up clean (registers 5 functions + 3 crons,
  connects to Redis). All 6 compose services now Up together for the first time.
- ✅ **PIPELINES — cross-workflow relation graph (new product feature).** Pure analyzer
  `relations/analyze.py`: `extract_relations(wf)` distils a workflow's `on:`/`uses:`/steps into a
  descriptor (workflow_run upstreams, reusable `calls`, `is_reusable`, dispatch listener, heuristic
  PR/dispatch `emits`); `build_pipeline_graph(items)` assembles a typed node/edge graph + connected
  components. **Precise** edges (workflow_run triggers, reusable calls, dispatch listeners) vs
  **heuristic** (`heuristic:true` — PR/dispatch senders pattern-matched from steps), flagged in UI.
  Persisted: new `workflow_relations(repo_id, path, name, descriptor)` table (migration **0009**,
  unique (repo_id,path)), upserted in `audit_repo` (so App + offline modes populate it). API
  `GET /api/v1/pipelines`. UI **Pipelines tab** (`PipelinesTab.tsx`) renders each chain as
  node-cards + typed edge badges. Seed has a demo cross-repo chain (CI→triggers→Deploy→opens-PR→
  infra-terraform; web-frontend Release→calls→infra-terraform Apply). Tests: `test_relations.py`.
  Validated live: graph endpoint returns the expected edges + 2 components.
- ✅ **OFFLINE MODE (no GitHub App)** — `ACTIONSPLANE_OFFLINE_REPOS` (comma-sep owner/repo or
  URLs) + optional plain `ACTIONSPLANE_GITHUB_TOKEN`. New `offline/sync.py` (`parse_repo_spec`,
  `sync_repo`, `sync_offline`) pulls each repo's meta+runs and runs `audit_repo` over the
  **public** REST API; `GitHubClient` now accepts `token=None` (no Authorization header) + a new
  `get_repo_meta`. API: `lifespan` startup background sync when offline, `GET /api/v1/mode`,
  `POST /api/v1/offline/sync`. UI: `useMode` hook → header shows **Offline + Sync button**
  (`SyncButton.tsx`) instead of the live pill; Sync invalidates all queries. Config exposes
  `offline_repo_list`/`offline_mode` properties. Tests: `test_offline.py` (spec parsing, unauth
  client, `sync_repo` end-to-end via MockTransport+sqlite). **Validated live against real GitHub**
  (`actions/checkout`: id+5 runs+7 workflow files fetched unauthenticated). Needs only API+PG.
- ✅ **`audit local [PATH]` CLI** — scans a whole local repo's `.github/workflows/*.yml`, runs
  the full audit suite, exits non-zero on findings (CI/pre-commit gate); `--exit-zero` to override.
  `tests/test_cli_local.py`. Dogfooded on this repo (finds the missing-concurrency low).
- ✅ **Richer seed** — `scripts/seed_local.py` now 4 repos (one clean), a `workflows` row per repo
  so the **metrics panel populates**, multiple runs with real durations (success/failure/in-progress/queued),
  findings across all severities, and varied drift. Frontend compose port moved to **:3001**
  (3000 is taken by the sibling `gpuplane-ui`). Full stack now runs via `docker-compose.full.yml`.
- ✅ **GitHub deep links + re-run + logs** — run grid + drawer link to the run/commit/branch/job
  on GitHub (pure client-side URL builders from owner/name + ids). **Re-run**: `client.rerun_run`
  → `POST /repos/{o}/{r}/actions/runs/{id}/rerun`; `executor/actions.py:rerun_run` service;
  `POST /api/v1/runs/{id}/rerun` endpoint (404 unknown run, 503 App-not-configured, 502 GitHub
  reject — all graceful). Needs **`actions: write`** (new scope beyond read-only observe; documented
  in USER_GUIDE + this file). MockTransport test in `tests/test_rerun.py`. Job "Logs" links point
  at GitHub's per-job log view (no in-app log proxy — deliberate, avoids a heavy backend stream).
- ✅ **Real bug found by running it live: scorecard + metrics endpoints 500'd.** `api/app.py`
  did `SomeOut(**obj.__dict__)` on `Scorecard` / the metrics summary, both `@dataclass(slots=True)`
  → no `__dict__` → `AttributeError`. Never caught because nothing exercised these endpoints
  against real rows (the exact "import/parse/MockTransport only" gap the staff review named).
  Fixed to `asdict(obj)` (app.py:117, :162). Added `tests/test_api_endpoints_db.py` — the
  project's first DB-backed *API* test (seeds a sqlite DB, calls the endpoint coroutines, asserts
  serialization). Also made `upsert_finding` dialect-portable and gave the autoincrement
  BigInteger PKs a `.with_variant(Integer, "sqlite")` so the whole ORM is now sqlite-testable
  (Postgres DDL unchanged — migrations own the real schema).
- ✅ **User-facing docs + local onboarding** — `docs/USER_GUIDE.md` (run locally two ways, seed,
  add real repos via the GitHub App + smee tunnel, API/CLI/campaign usage, config table,
  teardown, k8s). `scripts/seed_local.py` fills the "no manual add-repo path" gap for local
  testing (writes a demo installation/repos/runs via the same upserts the worker uses;
  idempotent; works on PG or a throwaway sqlite file). Makefile: `up-full`, `seed`. README
  refreshed (was stale at "83 tests"; now 98 + accurate v1.1 status). **Repo onboarding is
  install-driven by design — there is no `POST /repos`; documented, not "fixed."**

DONE in v1.1 (session 3, all from `docs/staff-review.md`):
- ✅ **Token cache race** fixed — per-installation `asyncio.Lock` + double-checked locking in `github/factory.py`; 10-coroutine herd collapses to 1 mint (test_factory_race).
- ✅ **ETag / 304 caching** + **Retry-After / secondary-rate-limit backoff** in `github/client.py` (`_get_json` helper; applied to `list_workflow_runs` + `list_workflow_files`).
- ✅ **Ingestor hardening** — body-size cap (10 MiB → 413), `json.loads` guard (→ 400), `X-GitHub-Delivery` dedup table (migration 0006 `processed_deliveries`); duplicate delivery acks but doesn't re-enqueue side effects.
- ✅ **SARIF emit + Code Scanning upload** — `audit/sarif.py` (SARIF 2.1.0; `partialFingerprints` reuse the existing `Finding` fingerprint for alert dedup) + `github/client.upload_sarif` (gzip+base64). Headline find→fix bridge is now wired.
- ✅ **Pin classifier residual** — new `PinState.UNKNOWN_REF` for `@stable` / `@release-2024`; engine emits HIGH severity.
- ✅ **Partial index** for org-wide severity scorecard — migration 0007 `(severity, last_seen_at DESC) WHERE resolved_at IS NULL`.

DONE in session 2: ✅ API bearer auth · ✅ findings SQL filter+repo index · ✅ bounded concurrency · ✅ token-expiry-aware cache · ✅ apply hardening · ✅ GitLab unknown-include flagged.

DONE in session 5 (the rest of the v1.1 backlog):
- ✅ **Pagination** — generic `GitHubClient._get_paginated` walks RFC-5988 `rel="next"`; `_get_cached`
  keys the ETag cache by URL+params (fixed a latent cross-page collision) and returns headers.
  `list_workflow_runs` walks pages bounded by `max_runs` (logs truncation); `list_workflow_files`
  Link-walks too. Tests: two-page concat + max_runs cap (`tests/test_github_client.py`).
- ✅ **SARIF orchestration end-to-end** — `audit/sarif_service.py` (`upload_repo_sarif` injected-client
  + `upload_sarif_for_repo` mints/gates), API `POST /repos/{id}/sarif/upload`, worker `_maybe_upload_sarif`
  after every `audit_repo`. New flag `security_events_enabled` (default false). Empty result-sets
  upload on purpose (closes resolved alerts). Test: `tests/test_sarif_service.py` (sqlite+MockTransport).
- ✅ **SSE disconnect handling** — `subscribe(conn=...)` injectable + owns-only-what-it-opened;
  API `/events/stream` `aclose`s the bus stream in `finally`, checks `request.is_disconnected()`,
  `ping=15`. Test drives the cleanup path with a fake pubsub (`tests/test_event_bus.py`).
- ✅ **OTel end-to-end** — `observability/tracing.py`; FastAPI instrumented (api+ingestor);
  `enqueue_event` injects W3C carrier, `process_event` continues the trace; spans in `audit_repo`
  + `sarif.upload`. One trace ingest→worker→audit→SARIF. Off by default, import-safe. Test:
  `tests/test_tracing.py` (in-memory exporter proves parent/child + same trace_id).
- ✅ **hypothesis property tests** — `tests/test_property.py` (classify + pin-edit invariants).

STILL LEFT (post-v1.1):
1. **Live validation** — the ONLY remaining backlog item. Never run against real GitHub (App +
   webhook tunnel). User will test ~next day. Will produce the HTTP cassette corpus for write-path
   tests. Real-Postgres half already validated (session 4).
2. **Job-upsert ordering** — `upsert_job` has the same latent out-of-order bug as `upsert_run` had,
   but no monotonic `updated_at` on the `workflow_job` payload — needs a status-rank gate, deferred.
3. **Big bet (directions-research):** orchestration + evidence plane on top of GitHub's 2026 native
   primitives (lock-files, scoped secrets, egress firewall, Actions Data Stream).
4. **Other directions:** MCP server (writes=gated PRs); audit AI-authored workflow changes;
   over-scoped/inherited-secret audit; CI FinOps. More edit ops: bump-pins, set-permissions,
   inject-step. GitLab observe/edit pillars (deferred to v2 per staff-review). Possible Pipelines
   v2: intra-workflow job-`needs:` DAG drill-in inside the run drawer.

## 5. Key decisions / conventions
- **Pure core, thin I/O:** audit/drift/metrics/operations are side-effect-free over typed models; services do the I/O. Maximizes testability.
- **Two YAML representations:** Pydantic AST for *analysis*; ruamel round-trip for *edits* (preserves comments/format → reviewable diffs).
- **Idempotent everything:** GitHub-id upserts for runs/jobs; fingerprint upsert for findings (sha256 of repo:path:type:ref) + resolve-stale lifecycle.
- **All edits via PR**, dry-run→apply, apply gated by `bulk_edits_enabled` + human trigger; never write to main.
- Resolver injection keeps pin-shas pure (pre-resolve refs → dict → sync rewrite).
- Lint: ruff (E,F,I,UP,B,SIM,ASYNC,RUF), line-length 100. Per-file-ignores: B008 for cli/api (typer/fastapi idiom), migrations relaxed.

## 6. SANDBOX GOTCHAS (important when resuming here)
- **On the user's Windows machine / VS Code (session 4 onward):** no shim needed. Setup that
  works: `uv venv --python 3.12` then `uv pip install -e ".[dev]" aiosqlite`, run with
  `PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q` (98 green, ~1.5s). `uv` pulls a real
  CPython 3.12.13, so `StrEnum`/`datetime.UTC` exist natively. Docker is available here but the
  suite is hermetic (no docker/PG/Redis needed). The `/tmp/shim` + Python-3.10 notes below apply
  only to the older Cowork sandbox.
- **Python is 3.10 in the [Cowork] sandbox; project targets 3.12.** `StrEnum` (3.11+) and `datetime.UTC` (3.11+) don't exist on 3.10. A shim at `/tmp/shim/sitecustomize.py` backfills both; run tests with `PYTHONPATH="/tmp/shim:src"`. **/tmp is cleared between sessions** — recreate the shim:
  ```
  mkdir -p /tmp/shim && cat > /tmp/shim/sitecustomize.py <<'EOF'
  import enum
  if not hasattr(enum,"StrEnum"):
      class StrEnum(str,enum.Enum):
          def __str__(self): return str(self.value)
      enum.StrEnum=StrEnum
  import datetime as _dt
  if not hasattr(_dt,"UTC"): _dt.UTC=_dt.timezone.utc
  EOF
  ```
- `uv` can't fetch Python 3.12 (network-restricted); npm registry blocked. Deps installed ad hoc via `pip install --break-system-packages`: pydantic pydantic-settings fastapi ruamel.yaml typer rich pyjwt[crypto] sqlalchemy httpx arq alembic redis sse-starlette cryptography pytest pytest-asyncio ruff.
- **`plan.md` was read-only** (copied from upload); `chmod u+w plan.md` then write works.
- The sandbox mount sometimes lags/permission-denies on files written via the Windows file tools; writing via bash heredoc to `/sessions/.../actionsplane/...` is the reliable path and reflects back to the user's files.
- No live Postgres/Redis/GitHub in sandbox → DB/network paths are import/parse/MockTransport-verified only.

## 7. How to run / verify
```
cd <project>; export PYTHONPATH="/tmp/shim:src"
python -m ruff check . && python -m ruff format --check .
python -m pytest -q          # 129 tests
# real run (on a real machine): make install && make up && make migrate && make api / ingestor / worker
```
After editing portfolio markdown: `cd ~/Desktop/Claude/Projects && python scripts/generate_html.py`.

## 8. Research docs to reread
- `docs/feature-research.md` — backlog + differentiators (SARIF find→fix is #1).
- `docs/review-findings.md` — security/perf review; remaining: fetch concurrency/rate-limits.
- `docs/multi-ci-research.md` — GitLab next, avoid Jenkins (Groovy), provider-abstraction sketch.
- `docs/review-findings-2.md` — second security/perf review (apply-path + rate-limit items).
- `docs/directions-research.md` — landscape shift (GitHub 2026 primitives), new bets (MCP, AI-workflow audit), best-practices checklist.

## 9. Deployment assets (added session 2)
- `deploy/docker-compose.full.yml` — one-command local sandbox (PG+Redis+migrate+api+ingestor+worker).
- `deploy/docker/` — per-component Dockerfiles: `Dockerfile.api|ingestor|worker` (shared build, distinct CMD) + `Dockerfile.frontend` (Vite build → nginx, `nginx.conf` proxies /api + SSE).
- `deploy/k8s/` — kustomize manifests: namespace, configmap, secret.example (split: env secret + github-key pem secret), postgres StatefulSet, redis, migrate Job, api/ingestor/worker Deployments (+svcs), frontend, Ingress, kustomization (pins 4 images). Non-root, readOnlyRootFS + /tmp emptyDir, probes, resource limits. Worker = 1 replica (cron). See `deploy/k8s/README.md`.
- `deploy/helm/actionsplane/` — Helm chart (Chart.yaml, values.yaml, _helpers.tpl, configmap, gated postgres/redis, migrate as pre-install/upgrade **hook**, api/ingestor/worker/frontend, gated ingress, NOTES.txt). Images/replicas/resources/ingress are values-driven; secrets referenced by name (existingSecret + existingGithubKeySecret).
- `docs/k8s-architecture.md` — Mermaid services-level topology diagram + prose legend.
- NOT runnable in this sandbox (no docker/kubectl/kustomize/helm) — validated by YAML parse, kustomize resource resolution, and Helm template brace/include checks; `kubectl apply -k --dry-run=server` and `helm lint`/`helm template` on a real machine are the next checks.
