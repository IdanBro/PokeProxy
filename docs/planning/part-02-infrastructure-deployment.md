# Part 2 — Infrastructure & Deployment

## What I am actually solving

Part 1 made the service correct. It still only runs on a laptop against `localhost`: rules point at `http://localhost:8001`, the config path is relative, the mock downstream binds `127.0.0.1`, and the secret lives in a `.env` file.

Part 2 makes it a deployable unit: a production image, a Helm chart for PokeProxy + Redis + mock downstream, probes that encode the Part 1 reliability decisions, resource governance, container hardening, and a secret that never enters git in plaintext.

Five Part 1 items were explicitly scoped to here. This is where they close:

| ID | Gap | Closes in |
|---|---|---|
| H6 | `rules.json` hardcodes `http://localhost:8001/pokemon`; relative config path; mock binds `127.0.0.1` | steps 1, 3, 5 |
| H1 consequence | Rules load once at startup, so a ConfigMap edit is silently inert | step 5 (`checksum/config`) |
| H7 (K8s half) | App drains correctly; `preStop` + grace period missing | step 5 |
| L6 | `mock_service` unpackaged, not containerized | step 3 |
| R4 + M6 | Config failure emits a stray uvicorn traceback; `POKEPROXY_PORT` is dead config | step 2 |
| M2 (partial) | Edge body-size cap as defense-in-depth | step 8 |

## Approach

Same loop as Part 1: one small thing at a time, verified by execution before it is written down.

Order is deliberate — **make it build, make it start, make it deployable, make it survive Kubernetes, then measure.** Each step's verification has to fail if the step is wrong, which is why the failure modes are induced rather than assumed.

## Technologies chosen

| Area | Choice | Why |
|---|---|---|
| Control shell | WSL Ubuntu + bash | Part 5's bootstrap must also run on a Linux CI runner; PowerShell would mean two divergent scripts |
| Local Kubernetes | **k3d** | `k3d cluster create/delete` is scriptable — the deciding Part 5 constraint. Lightest control plane, which matters against 7.6 GiB once Part 4 lands. Traefik + ServiceLB built in, so the E2E gets a real URL instead of `port-forward` glue. k3s is a shipping production distro |
| Packaging | **Helm**, one chart, local + prod values | One approach for both environments. `--atomic --wait` auto-rolls-back a failed upgrade and `helm rollback` has revision history — half of Part 3's rollback story for free |
| Base image | `python:3.13-slim-bookworm`, multi-stage | glibc ⇒ `protobuf`/`hiredis` install as manylinux wheels. Shell present for 3 AM debugging |
| Dep install | uv 0.12.5, copied from `ghcr.io/astral-sh/uv` | Matches the Part 1 toolchain. `--frozen` makes a stale `uv.lock` a build failure |
| Redis | Templated in our chart, official `redis:7-alpine` | See alternatives below |
| Secrets | Sealed Secrets + a pinned sealing key | Encrypted value is safe in git; complete GitOps story for Part 3 |
| Ingress | Traefik (k3d default), `/stream` path only | Zero install. Not exposing `/health`/`/ready`/`/stats` is a free partial mitigation of M5 |

## Alternatives I considered

**Docker Desktop Kubernetes.** Already installed and a context already existed, so it was the zero-effort option. Rejected because its cluster lifecycle is a GUI toggle — it cannot be created or torn down from a script, which fails Part 5 outright. That requirement drove the whole choice.

**kind.** Genuinely close, and closer to vanilla upstream — if the target were EKS it would be the more faithful rehearsal. Rejected on the heavier control plane and on needing ingress-nginx installed before there is a working URL. If someone pushes back on k3s' non-standard defaults (Traefik, klipper-lb, local-path), kind is the answer I would switch to, and nothing in the chart's `base` depends on k3s.

**minikube.** Heaviest, and driver variability on Windows adds a failure mode for no gain.

**Kustomize.** My original recommendation. `configMapGenerator`'s content-hash naming solves the H1 rules-restart problem for free, `kustomize edit set image` is the natural GitOps image-bump primitive, and it ships inside kubectl. Overruled on consistency — one packaging tool across local and prod. Helm's `checksum/config` annotation and a values-file image bump are equivalent in effect, and `--atomic` rollback is a real advantage in the other direction.

**Bitnami Redis chart.** The de-facto choice, and my first instinct was to use it rather than hand-roll infrastructure. Rejected on two grounds. First, the August 2025 catalog change moved its versioned images to `docker.io/bitnamilegacy/*` — archived, no security patches — and charts stopped publishing as OCI artifacts, so pinning it means running a frozen unpatched image or moving to a commercial tier. Second, even `architecture: standalone` brings a StatefulSet, a PVC, an auth Secret, sentinel/replication/metrics templates and a ~2500-line values file, ~90% of which we would disable. What we need is one Deployment, one Service, `maxmemory` + LRU, and an `emptyDir`.

The general rule "don't hand-roll infrastructure" is right, and for anything stateful or HA I would use a chart or an operator without hesitating. The claim here is narrow: **a stateless ephemeral cache is the case where a chart's value is lowest and its cost is highest** — and the setting that matters most, `maxmemory` strictly below the container memory limit so Redis evicts under LRU instead of being OOMKilled, is one I want to set directly rather than layer under someone else's defaults.

**Alpine base.** musl means `hiredis`/`protobuf` may compile from source — slower builds and more failure surface for ~50 MB.

**Distroless.** Debian's distroless Python is 3.11 and `requires-python >=3.13`. No shell when it matters.

**Redis with persistence / as a StatefulSet.** Part 1 decided the cache is best-effort and must not gate readiness. Persistence would be a false durability promise, and a PVC adds a storage-class dependency to the bootstrap.

**Mock downstream in the production image.** Ships a test double into production. Same objection applies to the chart, hence `mockDownstream.enabled: false` in prod values.

**Omitting CPU limits.** My preference — CFS throttling on a latency-sensitive proxy shows up as tail latency, which then poisons Part 4's alert thresholds. Overruled: limits are set at 2× requests. Mitigation is to set the *requests* from measurement rather than guesswork.

## Order of work

| # | Step | State |
|---|---|---|
| 1 | `app/Dockerfile` + `.dockerignore`; build and verify standalone | **Done** |
| 2 | `src/pokeproxy/__main__.py` config preflight (R4 + M6) | **Done** |
| 3 | `Dockerfile.mock` + `/health` on `mock_service` (L6) | Not started |
| 4 | Helm chart skeleton: `Chart.yaml`, values, namespace + PSA, ServiceAccounts | Not started |
| 5 | Workload templates: redis, mock, pokeproxy — probes, resources, securityContext, `checksum/config`, rules via `toJson` (H6, H7) | Not started |
| 6 | k3d cluster definition, `k3d image import`, first `helm upgrade --install` | Not started |
| 7 | Sealed Secrets: controller, pinned sealing key, `seal-hmac.sh` | Not started |
| 8 | Ingress + Traefik body cap (M2) + NetworkPolicy | Not started |
| 9 | Rollout / termination / measurement pass | Not started |
| 10 | `values-prod.yaml` + issue write-ups + WORKLOG | Not started |

## Why the sealing key has to be pinned

A SealedSecret is encrypted against *that cluster's* controller keypair, and our k3d cluster is ephemeral by design — `k3d cluster delete` is the Part 5 teardown. On startup the controller adopts Secrets labeled `sealedsecrets.bitnami.com/sealed-secrets-key: active`; **finding none, it mints a fresh keypair**, so a new cluster would otherwise mean a committed SealedSecret that no longer decrypts.

So the bootstrap generates an RSA keypair into a gitignored `.secrets/` file, applies it into `kube-system` *before* installing the controller, and installs with `keyrenewperiod=0`. This is the project's own documented backup/restore procedure, run proactively instead of after a disaster.

`kubeseal --raw` emits a single ciphertext string, so the encrypted value lives in `values-local.yaml` and the chart templates the `SealedSecret` from it. Re-running the bootstrap does not churn git.

Honest limitation: Kubernetes Secrets are base64, not encrypted, at rest in etcd by default. Sealed Secrets protects the value **in git**, which is the threat model here. Encryption-at-rest is the complementary control, not something this replaces.

## Constraints this sets for later Parts

- **The chart is the single deployable unit.** Part 3's Argo CD gets one Application; CI writes exactly one field (`image.tag`) back to git.
- **Immutable image tags, never `:latest`.** A rollback needs an addressable artifact.
- **The mock downstream stays single-replica with `Recreate`.** Its `received_pokemon` list is in-process, so the Part 3 E2E breaks the moment two pods coexist — including during a rolling update.
- **The Part 3 E2E must use a unique payload per run.** M4's dedup replays the cached response instead of forwarding, so a repeated payload never reaches downstream and the check fails against a *healthy* deployment.
- **Labels are the join key.** `app.kubernetes.io/{name,component,part-of}` must be consistent or Part 4's ServiceMonitor selectors and dashboards have nothing to group on.
- **NetworkPolicy must allow scraping from `monitoring`.** Default-deny lands in step 8; Part 4 will need the exception.
- **A second container port is reserved for ops endpoints** (`/metrics`, `/stats`) so M5 can move them off the public Service in Part 4.
- **Every step is scriptable and idempotent** so Part 5 assembles rather than rewrites.

## Definition of done for Part 2

- Image builds reproducibly, runs non-root on a read-only root filesystem, and contains no dev dependencies. **Met (step 1).**
- A rules change in values actually rolls the pods. Not yet.
- Probes encode the Part 1 decisions: liveness touches nothing external; Redis never gates readiness. Not yet.
- A rolling restart under live traffic drops zero requests. Not yet.
- The HMAC key exists in git only as ciphertext, and the same ciphertext decrypts on a freshly recreated cluster. Not yet.
- Resource numbers are measured with `kubectl top`, not guessed. Not yet.
- The namespace *enforces* the security context rather than documenting it — a `runAsNonRoot: false` pod is rejected. Not yet.
- Anything not verified by execution is labelled as not verified.

## Step 1 — result

Built `pokeproxy:395479c` from `app/Dockerfile`, context `app/`.

| Check | Result |
|---|---|
| Build | 37.0s cold |
| Image size | **248 MB** |
| Runtime user | `uid=10001(pokeproxy) gid=10001(pokeproxy)` |
| Container start → `startup complete` | **2.55s** (22:47:18.330 → 22:47:20.875), including Docker's own start overhead |
| `--read-only --cap-drop ALL --security-opt no-new-privileges` | Serves normally; `docker diff` shows **only the rules bind-mount**, zero filesystem writes |
| Logs | JSON from the first line, uvicorn's own records included — import-time `setup_logging()` works in the container |
| Dev dependencies | `pytest` absent (`ModuleNotFoundError`); venv `bin/` holds only runtime entry points |
| SIGTERM | `shutdown started` → `shutdown complete` → exit **0**, 880 ms wall. uvicorn is PID 1 (exec-form `CMD`), so no signal-forwarding or zombie-reaping problem |

Ten-case functional probe against the running container, driven from a throwaway container of the same image:

| Case | Result |
|---|---|
| valid signature, non-matching pokemon | 200 `{}` (`no_rule_matched`) |
| valid signature, matching pokemon | 502 `downstream error` — **expected**, rules still say `http://localhost:8001` (H6, fixed in step 5) |
| missing signature | 401 |
| invalid signature | 401 |
| malformed protobuf | 400 |
| `/health`, `/ready` | 200 |
| `/stats` | 200, endpoint and outcome counters populated |
| Redis unreachable (nothing on 6379 in-container) | 3 × `WARNING cache lookup failed`, **zero 5xx** — C4 degradation holds inside the container |

The 2.55s startup contradicts the ~3.2s module-import figure measured over WSL's `/mnt/c` in Part 1, which is what I expected — that number was filesystem overhead, not the application. The `startupProbe` budget (30 × 1s) is set against the container figure.

`ghcr.io/astral-sh/uv:0.12.5` and `python:3.13-slim-bookworm` are pinned by tag, not digest. Digest pinning is stronger and is the right Part 3 addition once there is a bot to bump them.

## Step 2 — result

New `src/pokeproxy/__main__.py`: constructs `Settings` via `main.py`'s existing `_load_settings()`, then hands off to `uvicorn.run("pokeproxy.main:app", host="0.0.0.0", port=settings.pokeproxy_port, log_config=None)`. `Dockerfile`'s `CMD` switched from the uvicorn CLI to `["python", "-m", "pokeproxy"]`.

`log_config=None` is load-bearing, not cosmetic. `pokeproxy.main` installs the JSON logging config as an import-time side effect — clearing `uvicorn`/`uvicorn.error`/`uvicorn.access` handlers, setting `propagate=True`, disabling `uvicorn.access`. Importing `_load_settings` from `__main__.py` triggers that import before `uvicorn.run()` is called. Left at its default, `uvicorn.run()` calls its own `logging.config.dictConfig()`, which reinstalls handlers with `propagate=False` on those three loggers and silently undoes the JSON setup. Caught by `test_uvicorn_logging_config_is_disabled` and confirmed by a real container run — uvicorn's own `Started server process` / `Application startup complete` lines still render as JSON.

Also fixes R4 and M6:
- **R4.** `_load_settings()` now runs and can fail *before* `uvicorn.run()` starts any asyncio machinery, so a bad configuration produces exactly the one `CRITICAL` line and exit 1 — no lifespan `SystemExit` traceback riding along behind it.
- **M6.** `settings.pokeproxy_port` is now read by something. Previously validated and documented, never wired to the actual listener (which came from the CLI's hardcoded `--port 8000`).

`app/.gitattributes` added at the same time (repo root), forcing `eol=lf` on `*.sh`, `Dockerfile*`, `*.yaml/.yml`, `Makefile`. Unrelated to R4/M6 but folded in here rather than left for Part 5 to discover the hard way: `git add` had warned "LF will be replaced by CRLF" on every file touched in step 1, and a `.sh` script checked out with CRLF fails with `bad interpreter: /bin/bash^M` the moment it's run under WSL or in a container — exactly the kind of failure Part 5's bootstrap scripts would hit first.

Five new tests in `tests/test_entrypoint.py`, mocking `uvicorn.run` rather than binding a real port: bad config exits before `uvicorn.run` is called and logs one `CRITICAL` line; a custom `POKEPROXY_PORT` reaches `uvicorn.run`'s `port` kwarg; the default is 8000; the app import string is `"pokeproxy.main:app"`; `log_config=None` is passed.

Verified by execution, not asserted:

| Check | Result |
|---|---|
| `ruff check .` | All checks passed |
| `pytest -q` from `app/`, the repo root, `/tmp` | **106 passed** each time (101 → 106; M7-CWD's CWD-independence survives the new entrypoint) |
| Container: bad config (`POKEPROXY_HMAC_KEY` unset) | **1 line of output**, `CRITICAL configuration invalid, refusing to start`, exit code **1** — confirmed by `docker run --rm` with no other flags |
| Container: `POKEPROXY_PORT=9001` | `/health` answers on **9001**, log line reads `Uvicorn running on http://0.0.0.0:9001` |
| Container: SIGTERM on custom-port run | `shutdown started` → `shutdown complete`, clean exit, **1.21s** wall (includes Docker's own stop grace handling, consistent with the 880ms figure from step 1) |
| JSON logging through the new path | uvicorn's own startup lines still render as JSON objects — `log_config=None` did not regress step 1's logging behavior |

No change to `app/src/pokeproxy/main.py`, `proxy.py`, `config.py`, or any other request-path code — this step is entrypoint-only.
