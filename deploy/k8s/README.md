# Claude Duet on Kubernetes

Plain manifests, no Helm. Everything lands in the `claude-duet` namespace.

## Before you apply

1. **Build and push the two images.** The manifests reference `duet-api:0.1.0`
   and `duet-web:0.1.0`; retag them for your registry and update the `image:`
   fields in `api-deployment.yaml` and `web-deployment.yaml`.

   ```sh
   docker build -t duet-api:0.1.0 apps/api
   docker build -t duet-web:0.1.0 \
     --build-arg NEXT_PUBLIC_API_BASE=https://duet.example.com apps/web
   ```

   `NEXT_PUBLIC_API_BASE` is inlined by `next.config.mjs` at **build** time.
   Changing it in the ConfigMap afterwards does not change the browser bundle.

2. **Create the Secret.** `secret.example.yaml` is a template, not something to
   apply as-is.

   ```sh
   cp secret.example.yaml secret.yaml
   python -c "import secrets; print(secrets.token_urlsafe(48))"   # DUET_SECRET_KEY
   # edit secret.yaml, then keep it out of git
   ```

   `DUET_SECRET_KEY` encrypts the credential vault at `/app/.data/vault.enc`.
   Lose or rotate it and the existing vault **cannot** be decrypted — every
   connector credential has to be re-entered. Back it up separately from the
   volume.

3. **Set the real hostname** in `configmap.yaml` (`DUET_CORS_ORIGINS`,
   `NEXT_PUBLIC_API_BASE`) and `ingress.yaml` (`spec.rules[0].host`).

## Apply order

Config and storage first, then data services, then the apps, then the edge.

```sh
kubectl apply -f namespace.yaml          # 1. namespace (everything else is in it)
kubectl apply -f configmap.yaml          # 2. non-secret config
kubectl apply -f secret.yaml             # 3. your filled-in copy of secret.example.yaml
kubectl apply -f postgres.yaml           # 4. StatefulSet + headless Service
kubectl apply -f redis.yaml              # 5. optional cache / event bus
kubectl apply -f qdrant.yaml             # 6. optional vector store
kubectl apply -f api-deployment.yaml     # 7. API (waits on Postgres via initContainer)
kubectl apply -f web-deployment.yaml     # 8. web
kubectl apply -f ingress.yaml            # 9. routing
```

Or, once `secret.yaml` exists, the whole directory in one shot — `kubectl apply`
sorts by kind, so the ordering above is mostly a readability aid:

```sh
kubectl apply -f .
```

Steps 1-3 are the only hard prerequisites: the API pod will not start without
`duet-config` and `duet-secrets`.

## Verify

```sh
kubectl -n claude-duet get pods,svc,pvc
kubectl -n claude-duet rollout status deploy/duet-api
kubectl -n claude-duet logs -f deploy/duet-api

# health check without the ingress
kubectl -n claude-duet port-forward svc/duet-api 8787:8787
curl -fsS http://127.0.0.1:8787/api/health
```

## Notes and limits

- **`duet-api` runs one replica.** The vault sits on a ReadWriteOnce PVC
  (`duet-api-data`), so the Deployment uses `strategy: Recreate`. Scaling out
  needs a ReadWriteMany volume or the vault moved to a secret manager.
- **`duet-web` runs two replicas** and is stateless.
- **Redis is ephemeral** (`emptyDir`, no AOF/RDB). Delete `redis.yaml` and unset
  `DUET_REDIS_URL` to fall back to the in-process event bus.
- **Qdrant is optional.** With `DUET_QDRANT_URL` unset, semantic recall is off.
- **No HPA, PDB, NetworkPolicy or ServiceMonitor here** — deliberately minimal.
- All pods run `runAsNonRoot` with `allowPrivilegeEscalation: false` and all
  capabilities dropped. The API and Qdrant need a writable filesystem for their
  data directories, so `readOnlyRootFilesystem` is `false` for those two.
- `storageClassName` is left unset everywhere, so the cluster default is used.

## Teardown

```sh
kubectl delete -f ingress.yaml -f web-deployment.yaml -f api-deployment.yaml
kubectl delete -f qdrant.yaml -f redis.yaml -f postgres.yaml
kubectl delete namespace claude-duet     # also removes the PVCs and their data
```
