# ActionsPlane — User Guide

How to run ActionsPlane locally, get repositories into it, and use the observe / audit /
drift / edit features. For *why* it's built this way see [`../plan.md`](../plan.md); for the
runtime view see [`ARCHITECTURE.md`](ARCHITECTURE.md).

> **Honesty up front.** The read model (DB → API → UI), the audit/drift/edit *engines*, and
> the docker stack are exercised and — as of 2026-06-01 — validated against a real Postgres
> locally. The full **ingest → audit → SARIF → PR** loop needs live GitHub credentials and a
> public webhook endpoint, and is still pending first run against a real org (backlog item in
> [`memory.md`](memory.md) §4). Where something isn't yet wired, this guide says so.

---

## 1. Prerequisites

- **Docker Desktop** (for Postgres + Redis, and optionally the whole stack in containers).
- **[uv](https://docs.astral.sh/uv/)** for the Python toolchain, *or* just Docker if you only
  want the containerised stack.
- That's it for local use. A **GitHub App** is only needed to ingest real data (§5).

---

## 2. Run it locally

There are two ways to run, depending on whether you want to hack on the code.

### Option A — everything in Docker (closest to prod)

Brings up Postgres + Redis + the migration job + API + ingestor + worker, all wired together:

```sh
docker compose -f deploy/docker-compose.full.yml up --build
```

Then:

```sh
curl localhost:8000/healthz        # API liveness   → {"status":"ok",...}
curl localhost:8001/healthz        # ingestor liveness
curl localhost:8000/api/v1/repos   # [] until you seed (§4) or install a GitHub App (§5)
open  localhost:8000/docs          # OpenAPI / Swagger UI
```

Stop it with `Ctrl-C`, or `docker compose -f deploy/docker-compose.full.yml down`. Add `-v` to
also wipe the Postgres volume.

> The GitHub App env vars are optional here — without them the API/UI still serve the (empty)
> read model and the worker's cron sweeps no-op cleanly.

### Option B — host dev loop (fast reload)

Run only Postgres + Redis in Docker; run the Python processes on the host so edits reload
instantly. This is the loop used to build and validate the project.

```sh
# one-time: create the venv and install dev extras (Python 3.12)
uv venv --python 3.12
uv pip install -e ".[dev]" aiosqlite

# infra
make up                            # docker compose: Postgres + Redis only

# point the app at the local infra, then migrate
export ACTIONSPLANE_DATABASE_URL="postgresql+asyncpg://actionsplane:actionsplane@localhost:5432/actionsplane"
export ACTIONSPLANE_REDIS_URL="redis://localhost:6379/0"
.venv/Scripts/python.exe -m alembic upgrade head     # (Linux/macOS: .venv/bin/python)

# run the processes (separate terminals, or background them)
make api                           # FastAPI read API on :8000
make ingestor                      # webhook receiver on :8001
make worker                        # arq worker (crons + event processing)
```

On Windows the venv interpreter is `.venv/Scripts/python.exe`; on Linux/macOS it's
`.venv/bin/python`. The `make` targets use `uv run`, which resolves the same venv.

Copy `.env.example` to `.env` to set these once instead of exporting each time (`.env` is
gitignored).

---

## 3. Verify the suite (no infra needed)

The unit/integration suite is hermetic — no Docker, no network:

```sh
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q     # 98 passing, ~1.5s
PYTHONPATH=src .venv/Scripts/python.exe -m ruff check .
```

---

## 4. Get demo data in (no GitHub needed)

Because repo onboarding is install-driven (§5), a fresh DB is empty and the dashboard has
nothing to show. To populate a **demo installation + repos + runs** so you can explore the
API/UI locally, run the seed script:

```sh
# against the running Postgres (Option A or B):
export ACTIONSPLANE_DATABASE_URL="postgresql+asyncpg://actionsplane:actionsplane@localhost:5432/actionsplane"
PYTHONPATH=src .venv/Scripts/python.exe scripts/seed_local.py

# …or against a throwaway sqlite file (no Postgres at all):
ACTIONSPLANE_DATABASE_URL="sqlite+aiosqlite:///./local.db" \
  PYTHONPATH=src .venv/Scripts/python.exe scripts/seed_local.py
```

It writes a `demo-org` installation, two watched repos (`payments-api`, `web-frontend`), and a
handful of runs. It's idempotent — re-run it freely. Then:

```sh
curl localhost:8000/api/v1/repos
curl "localhost:8000/api/v1/runs?repo_id=5001"
```

> The seed only makes the **read model** non-empty (repos/runs/UI). It does **not** fetch
> workflow files or run audits — that needs a real GitHub token (§5/§6).

---

## 5. Add real repositories (the GitHub App flow)

**Repos are onboarded by installing the GitHub App — there is no manual "add repo" API or CLI
command, by design.** Onboarding follows GitHub's per-installation permission model: you install
the App on the org/repos you want, GitHub sends an `installation` / `installation_repositories`
webhook, and the worker upserts each granted repo with `watched = true`. From then on
`workflow_run` / `workflow_job` / `push` webhooks stream live data in, and the reconcile cron
(every 5 min) backfills anything a webhook dropped.

### 5.1 Create the App

Create a GitHub App at **https://github.com/settings/apps** (or an org's settings) with:

| Permission | Level | Why |
|---|---|---|
| Actions | Read **/ Write** | runs + jobs (Read); Write only if you want the dashboard's **Re-run** button |
| Metadata | Read | repo identity |
| Contents | Read **/ Write** | Write only if you enable bulk edits (PRs) |
| Pull requests | Write | open converge/fix PRs |
| Code scanning alerts (`security_events`) | Write | SARIF upload (optional; gated) |

Subscribe to events: **`workflow_run`, `workflow_job`, `push`, `installation_repositories`**.
Set the **Webhook URL** to your ingestor's `/webhook` and a **Webhook secret** (any random
string). Generate and download a **private key** (`.pem`).

### 5.2 Point ActionsPlane at the App

```sh
ACTIONSPLANE_GITHUB_APP_ID=123456
ACTIONSPLANE_GITHUB_APP_PRIVATE_KEY_PATH=/secrets/app.pem     # never inline the key
ACTIONSPLANE_GITHUB_WEBHOOK_SECRET=the-secret-you-set
```

The key is referenced by **path** (mounted from a secret in prod), never committed — `*.pem`
is gitignored.

### 5.3 Receive webhooks locally

GitHub can't reach `localhost`. For local testing of the *real* webhook path, use a tunnel —
GitHub's own recommendation is [smee.io](https://smee.io):

```sh
npx smee-client --url https://smee.io/your-channel --target http://localhost:8001/webhook
```

Set the App's Webhook URL to the smee channel. Now install the App on a repo and watch the
`installation_repositories` event create rows, followed by live `workflow_run` events.

### 5.4 Install it

Install the App on the target org/repos. Within seconds `GET /api/v1/repos` returns them, and
runs begin streaming. The ingestor verifies every payload's HMAC (fail-closed) and dedups by
`X-GitHub-Delivery`, so redeliveries are effectively-once.

---

## 5b. Offline mode (no GitHub App)

If you just want to **observe a set of public repos** without creating a GitHub App or exposing
a webhook, run in **offline mode**: give ActionsPlane a list of repos and it pulls their
workflows + recent runs over the public REST API. Read-only, no webhooks, no live updates — but
the full audit + drift + metrics views work, and a **Sync** button re-pulls on demand.

```sh
# point it at a list (owner/repo or full URLs); optional token for a higher rate limit / private repos
export ACTIONSPLANE_OFFLINE_REPOS="actions/checkout,pallets/flask,octocat/Hello-World"
export ACTIONSPLANE_GITHUB_TOKEN=ghp_xxx        # optional

make api        # or set the same vars in docker-compose.full.yml's x-app-env and `up`
```

On startup the API fetches each repo in the background; open the dashboard and the header shows
an **Offline** indicator with a **Sync** button (instead of the live "Live" pill). Click **Sync**
to re-pull. Unauthenticated public access is rate-limited (60 req/hr); a `GITHUB_TOKEN` raises
that to 5,000/hr and lets you include private repos the token can read.

> Offline mode needs only the API + Postgres — the ingestor/worker/Redis aren't required (no
> webhooks, no queue). It's the fastest way to point the dashboard at real repos.

## 6. Use it

### API (read model)

`GET /api/v1/...` (bearer token required if `ACTIONSPLANE_API_TOKEN` is set):

| Endpoint | Returns |
|---|---|
| `GET /repos` | watched repos |
| `GET /repos/{id}/workflows` | parsed workflows for a repo |
| `GET /runs?repo_id=&workflow_id=&branch=&status=&limit=` | run history |
| `GET /runs/{id}/jobs` | jobs for a run |
| `GET /workflows/{id}/metrics` | success rate, p50/p95 duration, queue, flake |
| `GET /findings?repo_id=&severity=&finding_type=` | open audit findings |
| `GET /audit/scorecard` | org-wide posture scorecard |
| `GET /drift?repo_id=` | template-binding drift severities |
| `GET /templates` · `POST /templates` | canonical workflow templates |
| `POST /repos/{id}/bindings` | bind a workflow file to a template |
| `POST /campaigns` | create a bulk-edit campaign (computes dry-run diffs) |
| `POST /campaigns/{id}/apply` | open PRs (needs `bulk_edits_enabled` + token) |
| `GET /events/stream` | SSE live run/job updates |

Full interactive docs at `localhost:8000/docs`.

### CLI

The local commands work today with no infra (parser + engines run in-process):

```sh
uv run actionsplane audit local .                                   # scan a whole repo's workflows
uv run actionsplane audit local /path/to/repo --exit-zero           # don't fail the shell on findings
uv run actionsplane audit all   --file .github/workflows/ci.yml     # full suite on one file
uv run actionsplane audit pins  --file .github/workflows/ci.yml     # classify every uses:
uv run actionsplane audit perms --file .github/workflows/ci.yml
uv run actionsplane drift --template canonical/ci.yml --against repo/ci.yml
uv run actionsplane campaign preview --op pin-shas --file .github/workflows/ci.yml
```

`audit local` finds every `.github/workflows/*.yml` under a repo, runs the full audit suite, and
**exits non-zero if anything is found** — drop it in a pre-commit hook or a CI step as a gate.

API-backed CLI commands (`campaign create|status`) talk to a running API at
`ACTIONSPLANE_API_URL` (default `http://localhost:8000`).

### Bulk edits (campaigns)

All edits go through **PRs** — never a direct write to `main` — and apply is gated twice
(fail-closed): both `ACTIONSPLANE_BULK_EDITS_ENABLED=true` *and* `ACTIONSPLANE_API_TOKEN` must
be set. The flow: create a campaign (dry-run computes per-repo diffs with no writes) → review
the diffs → apply (opens one PR per repo, reusing the dry-run-resolved SHAs so a tag retargeted
between preview and apply can't change what lands).

```sh
# create a pin-shas campaign over repo ids 5001,5002 (dry-run diffs computed immediately)
uv run actionsplane campaign create --name "pin everything" --op pin-shas --repos 5001,5002
uv run actionsplane campaign status <id>
# apply via the API once you've reviewed (requires the two gates above):
curl -X POST localhost:8000/api/v1/campaigns/<id>/apply -H "Authorization: Bearer $TOKEN"
```

### UI

The React dashboard lives in [`../frontend/`](../frontend/) (Vite + TanStack Query, SSE live
updates) — three tabs:

- **Runs** — run history with status, branch/commit chips, relative time + duration, a live
  connection indicator, and per-row deep links to the run on GitHub. Click a row to open the
  **run drawer**: facts, workflow metrics, jobs (each linking to its logs on GitHub), a **View
  on GitHub / Logs** pair, and a **Re-run** button (see below).
- **Security** — a posture score gauge, severity distribution bar, and the findings table.
- **Drift** — template bindings and their drift severity.
- **Pipelines** — the fleet-wide cross-workflow graph: `workflow_run` trigger chains, reusable-
  workflow calls (cross-repo), and heuristic PR/dispatch edges (marked `~`), grouped into chains.

The fastest way to see it is the full Docker stack, which now includes the frontend:

```sh
docker compose -f deploy/docker-compose.full.yml up --build
# → dashboard at http://localhost:3000  (nginx serves the build, proxies /api + SSE to the api service)
```

For a hot-reload dev loop against a host-run API (§2 Option B):

```sh
cd frontend
npm install            # first time only (needs Node 18+)
npm run dev            # http://localhost:5173, proxies /api → :8000
```

**Re-run:** the drawer's Re-run button POSTs `/api/v1/runs/{id}/rerun`, which calls GitHub's
re-run API. It needs the GitHub App configured **and the `actions: write` permission** (one step
beyond the read-only observe path). Without the App it fails gracefully with a clear message; it
won't do anything against seeded demo data.

---

## 7. Configuration reference

All settings are environment variables, prefix `ACTIONSPLANE_` (or a `.env` file):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local Postgres DSN | asyncpg DSN |
| `REDIS_URL` | `redis://localhost:6379/0` | queue + pub/sub |
| `GITHUB_APP_ID` | — | GitHub App id |
| `GITHUB_APP_PRIVATE_KEY_PATH` | — | path to the App `.pem` |
| `GITHUB_WEBHOOK_SECRET` | — | HMAC secret; **ingestor fails closed if unset** |
| `GITHUB_API_URL` | `https://api.github.com` | set for GHES |
| `API_URL` | `http://localhost:8000` | API base the CLI calls |
| `API_TOKEN` | — | if set, `/api/v1` requires `Authorization: Bearer` |
| `POLL_INTERVAL_SECONDS` | `300` | reconcile cron cadence |
| `FETCH_CONCURRENCY` | `8` | max repos swept in parallel |
| `BULK_EDITS_ENABLED` | `false` | opt-in gate for PR-writing campaigns |
| `SECURITY_EVENTS_ENABLED` | `false` | opt-in gate for SARIF → GitHub Code Scanning upload (needs `security_events: write`) |
| `OFFLINE_REPOS` | — | comma-separated public repos for offline mode (no App) |
| `GITHUB_TOKEN` | — | optional PAT for offline reads (higher rate limit) |
| `OTEL_ENABLED` | `false` | enable OpenTelemetry tracing (exports via OTLP) |
| `OTEL_ENDPOINT` | — | OTLP gRPC endpoint, e.g. `http://otel-collector:4317` |

### 7.1 Code Scanning (SARIF find→fix bridge)

With `ACTIONSPLANE_SECURITY_EVENTS_ENABLED=true` (and the App granted `security_events: write`),
every org audit also pushes the repo's open findings to GitHub Code Scanning, so they appear in
the **Security → Code scanning** tab alongside CodeQL/zizmor. You can also trigger one repo on
demand: `POST /api/v1/repos/{repo_id}/sarif/upload`. Findings dedup via `partialFingerprints`, so
re-runs update the same alerts and a cleaned-up repo (zero findings) closes its old alerts.

### 7.2 Tracing

With `ACTIONSPLANE_OTEL_ENABLED=true`, a webhook is one distributed trace across
ingest → worker `process_event` → `audit.audit_repo` → `sarif.upload` (W3C context is carried over
the arq queue). Point `ACTIONSPLANE_OTEL_ENDPOINT` (or the standard `OTEL_EXPORTER_OTLP_ENDPOINT`)
at any OTLP collector (Jaeger, Tempo, Honeycomb…). Off by default and import-safe — nothing changes
when it's disabled.

---

## 8. Teardown

```sh
make down                                              # stop Postgres + Redis (Option B)
docker compose -f deploy/docker-compose.full.yml down  # stop the full stack (Option A)
#   add -v to also delete the database volume
```

---

## 9. Deploying to Kubernetes

Manifests are provided but not yet applied against a live cluster (validated structurally only):

- **kustomize:** [`../deploy/k8s/`](../deploy/k8s/) — `kubectl apply -k deploy/k8s/`
- **Helm:** [`../deploy/helm/actionsplane/`](../deploy/helm/actionsplane/) —
  `helm install actionsplane deploy/helm/actionsplane/`
- Per-component Dockerfiles in [`../deploy/docker/`](../deploy/docker/); topology diagram in
  [`k8s-architecture.md`](k8s-architecture.md).

First real `kubectl apply --dry-run=server` / `helm lint` lives on a real cluster.
