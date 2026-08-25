# PokeProxy

A Pokemon-stream reverse proxy — HMAC-validated protobuf in, rule-matched JSON forwarded out, Redis-deduplicated — taken from "runs on a laptop" to a hardened, observed, CI/CD-delivered, one-command-bootstrapped Kubernetes service. `README_HOME_ASSIGNMENT.md` is the assignment brief; this file is deliverable 8 — how the submission is laid out and how the one-command bootstrap works.

## Zero to running

```bash
make up
```

One command, non-interactive, exits with a real status code: `preflight` (tools + versions + Docker + host port) → create the `pokeproxy` k3d cluster if it doesn't already exist → mint-and-seal the HMAC key (`seal-hmac.sh`, safe to re-run) → `tilt ci` (builds all three images, installs `kube-prometheus-stack`, deploys the Helm chart with `--atomic`, runs the real post-install E2E check, probes the ingress from the host).

`make up` is idempotent — re-running it reuses the existing cluster and only touches what changed: same cluster, unchanged pod ages, next Helm revision.

```bash
make down
```

`tilt down` (uninstalls what Tilt deployed) then `k3d cluster delete`.

**No `make`?** Every target is a one-line delegate to a script — `bash scripts/up.sh ci` (what `make up` runs) and `bash scripts/down.sh` (what `make down` runs) work standalone, no Makefile required. Both are equally supported; a minimal WSL Ubuntu install may not have `make` preinstalled.

## What you actually get

- **PokeProxy** (2 replicas), **mock-downstream**, **Redis** — the app stack from Part 2, hardened per Part 1 (non-root, read-only root filesystem, dropped capabilities, resource limits, health probes, a `PodDisruptionBudget` (`minAvailable: 1`, active whenever `replicaCount > 1`), `NetworkPolicy` default-deny with explicit allows).
- **A real post-deploy gate**, not a report: the Helm release install/upgrade carries `--atomic`. A post-install Job sends real signed traffic through the live proxy and asserts on the result; if it fails, Helm rolls the release back automatically, before `make up` ever returns success.
- **`kube-prometheus-stack`** (Prometheus, Grafana, Alertmanager) with a dashboard and three alert rules for the app, reachable at `http://localhost:8080/grafana/` once `make up` finishes.
- **A live debugging surface** (`make dev`, below) — not just a deploy target.

## Prerequisites

`scripts/preflight.sh` checks this for you — every `make` target that touches a cluster runs it first, and it fails loudly and specifically (names the missing tool or the exact problem, plus a fix) rather than dying three minutes later on a symptom. By hand, you need:

| Tool | Floor | Why |
|---|---|---|
| `docker` | 20.10+ | image builds, the container runtime under k3d |
| `kubectl` | 1.24+ | talks to the cluster |
| `helm` | 3.8+ | the chart, `--atomic` |
| `k3d` | 5.0+ | the `k3d.io/v1alpha5` cluster config this repo uses |
| `kubeseal` | 0.24+ | seals the HMAC secret |
| `git` | 2.20+ | sha-tagged builds |
| `tilt` | 0.30+ | local only — not needed for `make up-prod` |

Plus Docker actually running, and host port 8080 (8081 for prod) free — both checked live, not assumed.

## Every command

| Command | Does | When to use |
|---|---|---|
| `make up` | preflight → cluster (create if absent) → seal HMAC key → `tilt ci` | the one-command bootstrap; CI-shaped, exits when done |
| `make dev` | preflight → cluster (create if absent) → seal HMAC key → `tilt up` | interactive: web UI, live logs per resource, the debug buttons below |
| `make down` | `tilt down` → `k3d cluster delete` | tear down the local cluster completely |
| `make up-prod` | `bash scripts/bootstrap-prod.sh` | bring up the prod stand-in (its own k3d cluster + Argo CD) — see `deploy/README.md` |
| `make down-prod` | delete the prod stand-in cluster | tear down prod; leaves the prod sealing key on disk |
| `make status` | cluster/pods/Helm releases/ingress probe, read-only | "what's running right now" |
| `make help` | lists all of the above | — |

## `make dev` — the debugging surface

`make dev` opens Tilt's web UI (`http://localhost:10350`) with live logs and status per resource, plus four buttons for exercising the running stack without hand-typing `kubectl`/`helm`/`curl` every time:

| Button | Runs | Notes |
|---|---|---|
| **Send signed traffic** | `load_generator.py` against the live ingress, with `Requests/sec`/`Duration (s)` text fields | Real traffic, real cache growth (`redis DBSIZE` rises as requests land). Needs [`uv`](https://docs.astral.sh/uv/) locally — it syncs `app/.venv` on first run |
| **Flush Redis cache** | `redis-cli FLUSHALL` against the live pod | Drops `DBSIZE` to 0 |
| **Break rules.json (rollback demo)** | deploys a values overlay with a wrong rule reason, so the post-install E2E fails | Reproduces Part 3 scenario B on demand — `--atomic` rolls back automatically; see `deploy/README.md` for the evidence |
| **Restore rules.json** | a minimal `helm upgrade --reuse-values` re-asserting the real rules | Deliberately *not* a resource re-trigger — that forces a full `helm uninstall`+reinstall of the whole release, wiping Redis and briefly taking mock-downstream/Redis down too. This does neither |

## Repo layout

```
app/                      application source — see app/README.md
  Dockerfile, Dockerfile.mock, Dockerfile.e2e
  src/pokeproxy/           the proxy itself
  mock_service/            mock downstream, for local/CI use
  e2e/                     the post-deploy verification script
  scripts/load_generator.py

deploy/
  helm/pokeproxy/          the Helm chart (app + Redis + mock-downstream)
  envs/{local,prod}/       per-environment values (image tags, sealed HMAC ciphertext)
  k3d/                     k3d cluster configs (local + prod stand-in)
  k8s/                     namespace manifests (PSA labels)
  monitoring/              kube-prometheus-stack values
  argocd/                  the Application Argo CD reconciles, for prod
  README.md                deploy details: monitoring access, prod/Argo CD, CI promote, rollback

scripts/
  preflight.sh             tool/version/Docker/port checks
  up.sh, down.sh           what `make up`/`make down` actually run -- up.sh also mints/seals
                           the HMAC key (seal-hmac.sh) before handing off to Tilt
  seal-hmac.sh             mint-or-reuse the sealing key, seal the HMAC secret
  init-sealing-key.sh      one-time, manual prod key provisioning
  install-monitoring.sh    kube-prometheus-stack install, shared by local and prod
  bootstrap-prod.sh, down-prod.sh   the prod stand-in
  break-rules.sh, restore-rules.sh, status.sh   what the make/Tilt targets call

Tiltfile                  the local dev graph: namespace, monitoring, image builds, the
                           chart, per-workload status, the debug buttons
Makefile                  up / dev / down / up-prod / down-prod / status / help

docs/
  planning/                per-Part design docs — what was considered, what was chosen, why
  issues/                  one write-up per production issue found and fixed (Part 1)
```

## How it fits together (Parts 1–4, briefly)

- **Part 1** — `docs/issues/` has one write-up per issue found in the original code: problem, production impact, fix. `docs/planning/part-01-production-hardening.md` for the approach.
- **Part 2** — `deploy/helm/pokeproxy` is the chart: the app, Redis, mock-downstream, resource limits, health probes, secrets via Sealed Secrets. `docs/planning/part-02-infrastructure-deployment.md`.
- **Part 3** — CI in `.github/workflows/ci.yml`, CD via Argo CD for the prod stand-in, the post-deploy E2E gate (`app/e2e/e2e_check.py`, `--atomic`), rollback via `rollback.yml`. Operational detail and rollback evidence: `deploy/README.md`. Design and rejected alternatives: `docs/planning/part-03-cicd-gitops.md`.
- **Part 4** — metrics in the app, `kube-prometheus-stack`, one dashboard, three alert rules. Access details: `deploy/README.md`. Design rationale and threshold justifications: `docs/planning/part-04-observability.md`.
- **Part 5** (this file) — `docs/planning/part-05-automation.md` has the full design: the build order, every decision and rejected alternative, and the gate each step was verified against.

`docs/planning/AI_WORKFLOW.md` records the actual conversation flow with the AI tooling used to build this — corrections made, decisions taken, and why — per deliverable 9.
