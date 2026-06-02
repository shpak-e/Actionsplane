# Deploying ActionsPlane to Kubernetes

Starter manifests (kustomize) for a self-hosted deployment. Five workloads — `api`, `ingestor`,
`worker`, `frontend`, plus `postgres`/`redis` for evaluation (use managed data stores in prod).

## 1. Build & push the four images
From the project root (`deploy/docker/` has one Dockerfile per component):

```sh
REG=ghcr.io/itamar            # your registry
TAG=0.1.0
docker build -f deploy/docker/Dockerfile.api      -t $REG/actionsplane-api:$TAG .
docker build -f deploy/docker/Dockerfile.ingestor -t $REG/actionsplane-ingestor:$TAG .
docker build -f deploy/docker/Dockerfile.worker   -t $REG/actionsplane-worker:$TAG .
docker build -f deploy/docker/Dockerfile.frontend -t $REG/actionsplane-frontend:$TAG .
docker push $REG/actionsplane-api:$TAG   # …and the other three
```
(`api`, `ingestor`, `worker` share an identical build and differ only by `CMD`; the frontend is
a Vite build served by nginx that proxies `/api` + SSE to the api Service.)

Point the manifests at your registry/tag in one place:
```sh
cd deploy/k8s
kustomize edit set image \
  ghcr.io/itamar/actionsplane-api=$REG/actionsplane-api:$TAG \
  ghcr.io/itamar/actionsplane-ingestor=$REG/actionsplane-ingestor:$TAG \
  ghcr.io/itamar/actionsplane-worker=$REG/actionsplane-worker:$TAG \
  ghcr.io/itamar/actionsplane-frontend=$REG/actionsplane-frontend:$TAG
```

## 2. Create secrets (do NOT use secret.example.yaml as-is)
```sh
kubectl create namespace actionsplane
kubectl -n actionsplane create secret generic actionsplane-secrets \
  --from-literal=ACTIONSPLANE_DATABASE_URL='postgresql+asyncpg://actionsplane:PW@actionsplane-postgres:5432/actionsplane' \
  --from-literal=POSTGRES_PASSWORD='PW' \
  --from-literal=ACTIONSPLANE_GITHUB_WEBHOOK_SECRET='…' \
  --from-literal=ACTIONSPLANE_GITHUB_APP_ID='123456' \
  --from-literal=ACTIONSPLANE_API_TOKEN='…'
kubectl -n actionsplane create secret generic actionsplane-github-key \
  --from-file=github-app.pem=./actionsplane.private-key.pem
```
Prefer Sealed Secrets / External Secrets Operator / a cloud secret manager in real clusters.

## 3. Apply
```sh
kubectl apply -k deploy/k8s
# wait for the one-shot migration to finish, then the apps roll out:
kubectl -n actionsplane wait --for=condition=complete job/actionsplane-migrate --timeout=120s
kubectl -n actionsplane get pods
```
The migrate Job runs `alembic upgrade head` once (safe to re-run). The GitHub App private key is
mounted read-only at `/secrets/github-app.pem` (matches `ACTIONSPLANE_GITHUB_APP_PRIVATE_KEY_PATH`).

## Notes
- **Webhooks:** point your GitHub App's webhook URL at `https://<host>/webhook` (Ingress routes it
  to the ingestor). Add TLS via cert-manager.
- **Worker** runs a single replica on purpose — the arq cron sweeps (reconcile/audit/drift) must not
  be scheduled by multiple replicas. `api`/`ingestor`/`frontend` scale horizontally.
- Pods run **non-root**, `readOnlyRootFilesystem: true`, all caps dropped, with a writable `/tmp`
  emptyDir.
- **Bulk edits** stay disabled until you set `ACTIONSPLANE_BULK_EDITS_ENABLED=true` (ConfigMap) AND
  an `ACTIONSPLANE_API_TOKEN` (apply is fail-closed without it).
- Validated here by YAML parse + kustomize resource resolution only — run `kubectl apply -k --dry-run=server`
  against your cluster to confirm against live API schemas.
