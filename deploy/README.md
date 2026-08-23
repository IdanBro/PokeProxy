# Deploying PokeProxy locally

Manual, step-by-step version of what Part 5's one-command bootstrap will eventually automate. Every command below has been run against a real k3d cluster; none of it is aspirational.

## Prerequisites

- Docker Desktop, running
- `kubectl`, `helm`, `k3d`, `kubeseal`, `openssl` on `PATH`
- A POSIX shell (WSL on Windows — every command below has only been verified there)

## 1. Create the cluster

```bash
k3d cluster create --config deploy/k3d/cluster.yaml
```

Single server node, `localhost:8080` mapped to the ingress. `kubectl config current-context` should now be `k3d-pokeproxy`.

## 2. Build and import the images

Build at the exact commit you intend to deploy — a stale image tag on a redeployed sha is the most common failure mode in this project's history.

```bash
SHA=$(git rev-parse --short HEAD)
docker build --build-arg GIT_SHA=$SHA -t pokeproxy:$SHA -f app/Dockerfile app/
docker build --build-arg GIT_SHA=$SHA -t mock-downstream:$SHA -f app/Dockerfile.mock app/
k3d image import pokeproxy:$SHA mock-downstream:$SHA -c pokeproxy
```

## 3. Create the namespace

```bash
kubectl apply -f deploy/k8s/namespace.yaml
```

This carries the `pod-security.kubernetes.io/{enforce,audit,warn}: restricted` labels that make the security posture in the chart's Deployments an enforced invariant, not just a claim. Idempotent — safe to re-run.

## 4. Seal the HMAC secret

```bash
bash scripts/seal-hmac.sh
```

Generates (or reuses) a Sealed Secrets sealing key under the gitignored `.secrets/`, installs the Sealed Secrets controller, and writes sealed ciphertext into `deploy/helm/pokeproxy/values-local.yaml`. Safe to re-run: it only re-seals when a new key was actually generated (fresh clone or fresh cluster), never on every run.

## 5. Deploy

```bash
SHA=$(git rev-parse --short HEAD)
helm upgrade --install pokeproxy deploy/helm/pokeproxy \
  -n pokeproxy \
  -f deploy/helm/pokeproxy/values-local.yaml \
  --set components.pokeproxy.image.tag=$SHA \
  --set components.mock-downstream.image.tag=$SHA \
  --atomic --timeout 3m
```

`--atomic` rolls back automatically if the release doesn't reach a healthy state within the timeout.

## 6. Verify

```bash
kubectl get pods -n pokeproxy
curl -i http://localhost:8080/stream   # expect 401 — no signature on this plain request
```

For a real signed request, `app/scripts/load_generator.py` builds valid protobuf + HMAC payloads:

```bash
cd app && python scripts/load_generator.py --url http://localhost:8080/stream --rps 1 --duration 5
```

## Teardown

```bash
k3d cluster delete pokeproxy
```

Deletes the cluster and everything in it. `.secrets/sealing-key.yaml` and `deploy/helm/pokeproxy/values-local.yaml` are left on disk — re-running steps 1–5 reuses them if the key is still valid, or regenerates and re-seals automatically if not (step 4's idempotency).

## Production (`values-prod.yaml`)

Not deployed anywhere — no production cluster exists for this assignment. `helm lint` and `helm template` against it are clean, but it has never been applied to a live cluster. See `docs/planning/part-02-infrastructure-deployment.md` step 10 for what's deliberately left unfinished (real downstream URLs, a real image registry).
