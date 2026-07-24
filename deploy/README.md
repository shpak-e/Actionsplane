# Deploying ActionsPlane

Everything needed to run ActionsPlane, from a laptop sandbox to a hardened Kubernetes cluster.
Pick the path that matches where you are:

| Path | Use it when | Auto-updates? |
| --- | --- | --- |
| [Compose — build from source](#a-compose-build-from-source) | Hacking on the code locally | Rebuild on change |
| [Compose — prebuilt GHCR images](#b-compose-prebuilt-images-from-ghcr) | Evaluating / a small always-on box | ✅ via Watchtower |
| [Kubernetes — kustomize](#c-kubernetes-kustomize) | You run k8s and want raw manifests | ✅ via Keel (opt-in) |
| [Kubernetes — Helm](#d-kubernetes-helm) | You run k8s and want values-driven config | ✅ via Keel (opt-in) |

All paths run the same five workloads: **api** (REST + SSE, :8000), **ingestor** (webhook receiver,
:8001), **worker** (arq cron sweeps), **frontend** (nginx serving the Vite build, proxying `/api` +
SSE to the api), plus **postgres** and **redis** for evaluation — swap in managed data stores for
production.

---

## The images

Four images, one per app component, published to **GitHub Container Registry**:

```
ghcr.io/shpak-e/actionsplane-api
ghcr.io/shpak-e/actionsplane-ingestor
ghcr.io/shpak-e/actionsplane-worker
ghcr.io/shpak-e/actionsplane-frontend
```

`api`, `ingestor`, and `worker` share one build (`deploy/docker/Dockerfile.{api,ingestor,worker}`)
and differ only by their start command — the api image also carries `alembic` + `migrations/`, so
the migration step reuses it. The frontend is a separate nginx image.

### How they get built and pushed

The [`release` workflow](../.github/workflows/release.yml) publishes them — there is **nothing to
run by hand**:

| Trigger | Tags published | Arch | Attestation |
| --- | --- | --- | --- |
| Push to `master` | `edge`, `sha-<commit>` | amd64 | — |
| Push a semver tag `vX.Y.Z` | `X.Y.Z`, `X.Y`, `latest`, `sha-<commit>` | amd64 + arm64 | SLSA provenance |
| Manual dispatch | `edge` | amd64 | — |

- **`edge`** is the rolling build of `master` — every merge republishes it. Point a running deploy
  at `:edge` and it can auto-update (below).
- Cut a **release** with a tag: `git tag v0.1.0 && git push origin v0.1.0`.

> **First-time visibility gotcha.** New GHCR packages are **private by default**, so they won't show
> up (or pull without a login) until you flip them. After the first successful `release` run, go to
> `github.com/shpak-e?tab=packages`, open each `actionsplane-*` package → **Package settings** →
> **Change visibility → Public** (and optionally **Connect repository** to link it back here). Until
> then, authenticate before pulling:
> ```sh
> echo $GHCR_PAT | docker login ghcr.io -u shpak-e --password-stdin   # PAT needs read:packages
> ```

Verify a release's provenance:
```sh
gh attestation verify oci://ghcr.io/shpak-e/actionsplane-api:0.1.0 --owner shpak-e
```

---

## A. Compose — build from source

The full local sandbox. Builds every image from your checkout and runs the migration on startup.

```sh
docker compose -f deploy/docker-compose.full.yml up --build
open http://localhost:3001        # dashboard
```

Optional demo data (no GitHub App needed): `PYTHONPATH=src python scripts/seed_local.py`.

## B. Compose — prebuilt images from GHCR

Pulls the published images instead of building, and (optionally) **auto-reloads** them when a new
`:edge` is pushed — a bundled [Watchtower](https://containrrr.dev/watchtower/) polls GHCR every
5 minutes and rolls any updated container.

```sh
docker compose -f deploy/docker-compose.ghcr.yml pull
docker compose -f deploy/docker-compose.ghcr.yml up -d
open http://localhost:3001
```

Pin a version instead of tracking `edge`:
```sh
ACTIONSPLANE_TAG=0.1.0 docker compose -f deploy/docker-compose.ghcr.yml up -d
```

Turn auto-reload off (update by hand with `pull && up -d` instead):
```sh
docker compose -f deploy/docker-compose.ghcr.yml up -d --scale watchtower=0
```

Configuration (GitHub App, offline mode, Redis password, API token) is identical to the full stack —
set the vars in a `.env` file and pass `--env-file deploy/.env`, or export them before `up`. See the
header of [`docker-compose.full.yml`](docker-compose.full.yml) and [`.env.example`](../.env.example)
for the full list.

## C. Kubernetes — kustomize

Raw manifests in [`k8s/`](k8s/). Full walkthrough (secrets, ingress, webhooks) in
[`k8s/README.md`](k8s/README.md). The short version:

```sh
# images + tag are set in one place — k8s/kustomization.yaml (defaults to :edge)
kubectl apply -k deploy/k8s
kubectl -n actionsplane wait --for=condition=complete job/actionsplane-migrate --timeout=120s
```

Auto-update: install [Keel](https://keel.sh) and add `keel.sh/policy: force` +
`keel.sh/trigger: poll` annotations to the Deployments (the Helm chart does this for you — see
below).

## D. Kubernetes — Helm

Values-driven chart in [`helm/actionsplane/`](helm/actionsplane/). Image registry/tag live in
[`values.yaml`](helm/actionsplane/values.yaml) (`image.registry`, `image.tag`).

```sh
helm install actionsplane deploy/helm/actionsplane \
  --namespace actionsplane --create-namespace \
  --set image.tag=0.1.0                      # or leave the default "edge"
```

**Auto-update** (opt-in): if [Keel](https://keel.sh) is installed in the cluster, enable the
annotations and it will watch GHCR and redeploy when the tracked tag's digest changes — the k8s
equivalent of Compose's Watchtower:

```sh
helm upgrade actionsplane deploy/helm/actionsplane --reuse-values \
  --set keel.enabled=true            # tracks image.tag (use "edge" for continuous delivery)
```

---

## Hardening (all k8s paths, on by default)

- Pods run **non-root**, `readOnlyRootFilesystem: true`, all Linux capabilities dropped, writable
  `/tmp` only.
- **Default-deny NetworkPolicies** (enforced by a policy-capable CNI); scope ingress to your
  controller's namespace via `networkPolicy.ingressControllerNamespaceSelector`.
- **Redis auth** required; **Ingress TLS** on by default (cert-manager assumed).
- **Bulk edits stay disabled** until you set both `ACTIONSPLANE_BULK_EDITS_ENABLED=true` and an
  `ACTIONSPLANE_API_TOKEN` — apply is fail-closed without the token.
- The **worker runs a single replica** on purpose: its arq cron sweeps must not be double-scheduled.
  `api`/`ingestor`/`frontend` scale horizontally.
