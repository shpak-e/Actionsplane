# ActionsPlane — Kubernetes services-level architecture

How the components map to Kubernetes objects and how traffic flows between them. Manifests live
in `deploy/k8s/` (kustomize) and `deploy/helm/actionsplane/` (Helm chart); per-component images
are built from `deploy/docker/`.

```mermaid
flowchart TB
    gh["GitHub\n(webhooks in · REST + PRs out)"]:::ext
    user["Browser / CLI"]:::ext

    subgraph ns["Kubernetes namespace: actionsplane"]
        ing(["Ingress (nginx)\n/webhook · /api · /"]):::svc

        subgraph front["frontend"]
            fsvc(["Service :80"]):::svc --> fdep["Deployment\nnginx + React (x2)"]:::dep
        end
        subgraph api["api"]
            asvc(["Service :8000"]):::svc --> adep["Deployment\nFastAPI read API + SSE (x2)"]:::dep
        end
        subgraph ingr["ingestor"]
            isvc(["Service :8001"]):::svc --> idep["Deployment\nwebhook receiver (x2)"]:::dep
        end
        wdep["Deployment\nworker — arq, no Service (x1)\nprocess events + cron sweeps"]:::dep
        mig["Job (Helm pre-install/upgrade hook)\nalembic upgrade head"]:::job

        subgraph data["data stores (or managed)"]
            pg(["Service :5432"]):::svc --> pgs["StatefulSet\nPostgres + PVC"]:::dep
            rd(["Service :6379"]):::svc --> rds["Deployment\nRedis"]:::dep
        end

        cm[("ConfigMap\nACTIONSPLANE_*")]:::cfg
        sec[("Secret: env\nDB URL · webhook secret · API token")]:::sec
        key[("Secret: github-app.pem\nmounted /secrets")]:::sec
    end

    user -->|HTTPS| ing
    gh -->|"workflow_run / workflow_job /\npush / installation"| ing
    ing -->|/| fsvc
    ing -->|/api · SSE| asvc
    ing -->|/webhook| isvc
    fsvc -. proxies /api .-> asvc

    idep -->|enqueue| rd
    wdep -->|consume + publish| rd
    adep -->|SSE subscribe| rd
    adep -->|read model| pg
    wdep -->|upsert runs/findings/bindings| pg
    mig -->|migrate| pg
    wdep -->|"REST / open PRs\n(installation token)"| gh

    adep -. envFrom .- cm
    idep -. envFrom .- cm
    wdep -. envFrom .- cm
    adep -. envFrom .- sec
    idep -. envFrom .- sec
    wdep -. envFrom .- sec
    adep -. mounts .- key
    idep -. mounts .- key
    wdep -. mounts .- key

    classDef ext fill:#21262d,stroke:#8b949e,color:#e6edf3;
    classDef svc fill:#1f6feb22,stroke:#58a6ff,color:#e6edf3;
    classDef dep fill:#23863622,stroke:#3fb950,color:#e6edf3;
    classDef job fill:#9e6a0322,stroke:#d29922,color:#e6edf3;
    classDef cfg fill:#6e768166,stroke:#8b949e,color:#e6edf3;
    classDef sec fill:#da363322,stroke:#f85149,color:#e6edf3;
```

## Reading the diagram

**Ingress** is the only entry point. It fans out by path: `/` to the **frontend** Service (nginx
serving the React SPA, which also reverse-proxies `/api` + the SSE stream), `/api` to the **api**
Service, and `/webhook` to the **ingestor** Service so GitHub deliveries reach the receiver directly.

**Three stateless, horizontally-scaled tiers** sit behind Services — `frontend`, `api`, `ingestor`
(2 replicas each). The **worker** has *no* Service (nothing connects to it inbound) and runs as a
**single replica** on purpose: it owns the arq cron sweeps (reconcile / audit / drift), which must
not be double-scheduled.

**Data plane:** Redis is the seam between tiers — the ingestor *enqueues* events, the worker
*consumes* them and *publishes* live ticks, and the api *subscribes* to relay those ticks over SSE.
Postgres holds the event-sourced read model; the api reads it, the worker writes it. Both are shown
as in-cluster (a Postgres `StatefulSet` + PVC and a Redis `Deployment`) for evaluation; in
production disable them in values and point at managed services.

**Config & secrets:** a `ConfigMap` supplies the non-secret `ACTIONSPLANE_*` env; one `Secret`
carries env-injectable values (DB URL, webhook secret, API token) and a *separate* `Secret` carries
the GitHub App private key, mounted read-only at `/secrets/github-app.pem`. App pods run non-root
with a read-only root filesystem (plus a writable `/tmp`).

**Migration** runs as a Helm `pre-install`/`pre-upgrade` hook Job (`alembic upgrade head`) so the
schema is current before any app pod starts; re-running is a no-op.

**Egress to GitHub** is only from the worker (and the campaign executor it hosts): REST reads for
reconciliation/audit and PR creation, authenticated with short-lived per-installation tokens.
