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
| 3 | `Dockerfile.mock` + `/health` on `mock_service` (L6) | **Done** |
| 4 | Helm chart skeleton: `Chart.yaml`, values, namespace + PSA, ServiceAccounts | **Done** |
| 5 | Workload templates: redis, mock, pokeproxy — probes, resources, securityContext, `checksum/config`, rules via `toJson` (H6, H7) | **Done** |
| 6 | k3d cluster definition, `k3d image import`, first `helm upgrade --install` | **Done** |
| 7 | Sealed Secrets: controller, pinned sealing key, `seal-hmac.sh` | **Done** |
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

## Step 3 — result

New `app/Dockerfile.mock`, closing L6. Deliberately does not reuse `app/pyproject.toml`/`uv.lock` — `uv sync` against the shared lockfile would pull in `httpx`, `protobuf`, `redis[hiredis]` and `pydantic-settings`, none of which the mock service uses. Instead the builder stage creates a bare venv and `uv pip install`s `fastapi==0.135.1` and `uvicorn[standard]==0.41.0`, pinned to the exact versions already resolved in `uv.lock` so the two images can't silently drift apart on the framework version. Same non-root/read-only pattern as the primary image: `USER 10001:10001`, no `HEALTHCHECK` (kubelet's job).

`mock_service` isn't (and still isn't) packaged into a wheel — `PYTHONPATH=/app` in the runtime stage is what makes `mock_service.main:app` importable by uvicorn without one, which is the actual containerization decision L6 was asking for. Making it a proper installable package was the other option on the table; rejected as unnecessary ceremony for a container that only ever runs `uvicorn mock_service.main:app`.

`mock_service/main.py` gained `GET /health` — the "Redis does not gate readiness" probe design (M1) has nothing to check for a service with no dependencies, so this is a plain liveness endpoint. The `if __name__ == "__main__":` block (`uvicorn.run(app, host="127.0.0.1", ...)`) was deleted: the documented run path is always the `uvicorn` CLI (`app/README.md`'s Quick Start, and now `Dockerfile.mock`'s `CMD`), so the block was unexercised dead code, and its `127.0.0.1` bind was literally the localhost-assumption `main.py:34` reference in the H6 backlog row — worth removing rather than leaving as a second, unused, wrong-for-containers entrypoint. Per the standing code-style rule in `CLAUDE.md`, code superseded by the current change is removed as part of it rather than left behind.

Verified by execution:

| Check | Result |
|---|---|
| Build (cold) | 16.8s |
| Image size | **236 MB** |
| Runtime user | `uid=10001(mockdownstream) gid=10001(mockdownstream)` |
| `--read-only --cap-drop ALL --security-opt no-new-privileges` | Serves normally; `docker diff` shows **zero filesystem writes** |
| Bind address | `Uvicorn running on http://0.0.0.0:8001` — confirmed reachable via published port, not just loopback |
| `GET /health` | `{"status":"alive"}`, 200 |
| `POST /pokemon` → `GET /received` round-trip | Posted body and `X-Grd-Reason` header both land correctly in `/received` |
| `ruff check .` / `pytest -q` | Clean / **106 passed** — `mock_service` isn't pytest-covered (a test double testing a test double is circular), so this confirms no regression elsewhere, not new coverage |

## Step 4 — result

New `deploy/helm/pokeproxy/`: `Chart.yaml`, `.helmignore`, `values.yaml`, and `templates/{_helpers.tpl, namespace.yaml, serviceaccount.yaml}`. No workload manifests yet — those are step 5.

`values.yaml` declares one `components` map (`pokeproxy`, `mock-downstream`, `redis`), each with `enabled` and `serviceAccount.create` — this is what lets `values-prod.yaml` disable the mock later with `components.mock-downstream.enabled: false` rather than a separate templating path. Component keys are kebab-case, matching the Kubernetes resource names directly, so there's no camelCase-to-kebab-case mapping table to keep in sync.

`_helpers.tpl` defines the label/naming contract every later template consumes: `pokeproxy.component.fullname`, `.selectorLabels`, `.labels`, called as `include "pokeproxy.component.X" (dict "context" $ "component" "redis")`. One thing this caught immediately: the `pokeproxy` component's name collides with the chart name, and the naive `<release>-<component>` pattern would render `pokeproxy-pokeproxy` for a release also named `pokeproxy` (the name step 6 plans to use). `pokeproxy.component.fullname` special-cases a component whose name equals the chart name to use the bare release name instead — the same fix `helm create`'s own scaffold applies for its single main component, adapted here for a multi-component chart. Verified: `pokeproxy`, `pokeproxy-mock-downstream`, `pokeproxy-redis` — no stutter, and confirmed to still hold under a different release name (`myrelease` → `myrelease`, `myrelease-mock-downstream`, `myrelease-redis`).

`namespace.yaml` names itself `{{ .Release.Namespace }}` rather than inventing a separate `values.namespace` field — the two would only ever need to be identical by convention, so keeping one fewer value removes a way for them to silently disagree. Carries `pod-security.kubernetes.io/{enforce,audit,warn}: restricted`, turning the securityContext work in step 5 into an enforced invariant rather than a claim, as decided in the design phase.

**Corrected in step 6, was wrong here.** This section originally claimed templating a Namespace resource inside the chart and installing with `-n pokeproxy`, *without* `--create-namespace`, was a known-working Helm pattern (Namespace first in Helm's fixed apply order). Tested live against a real cluster in step 6: **it fails outright** — `Error: create: failed to create: namespaces "pokeproxy" not found`. Helm 3.15.4 requires the target namespace to exist before it applies anything, full stop; templating the Namespace inside the chart doesn't help. `--create-namespace` doesn't fix it either — it creates the namespace via a raw, untracked API call, and the chart's own Namespace resource then collides with Helm's ownership-metadata check (`namespaces "pokeproxy" already exists`), exactly the anti-pattern this section already correctly predicted. Net result: **`templates/namespace.yaml` was removed from the chart entirely.** The namespace (with its PSA and ownership labels) is now created declaratively outside Helm's release, via `kubectl create namespace --dry-run=client -o yaml | kubectl label --local -f - ... | kubectl apply -f -`, before `helm upgrade --install` runs. Full detail and the exact failing commands are in the step 6 write-up below.

`serviceaccount.yaml` renders one ServiceAccount per enabled component, each `automountServiceAccountToken: false` at the SA level — the pod-level `securityContext` field is the same decision applied again in step 5, belt-and-suspenders rather than redundant, since either alone is enough to prevent the mount.

Verified by execution:

| Check | Result |
|---|---|
| `helm lint . --strict` | Clean — only an informational "icon is recommended" note |
| `helm template pokeproxy . --namespace pokeproxy` | Renders 1 Namespace + 3 ServiceAccounts; PSA labels present; consistent `app.kubernetes.io/{name,component,part-of,instance}` on every component resource *(the Namespace resource itself was removed in step 6 — see the correction above; this row describes step 4's render at the time, not the chart's current shape)* |
| `components.mock-downstream.enabled=false` override | Mock's ServiceAccount correctly absent from the render, others unaffected |
| Different release name (`myrelease`) | Naming holds: `myrelease`, `myrelease-mock-downstream`, `myrelease-redis` — the fullname fix isn't hardcoded to one release name |

No Python touched — chart-only step, so the test suite wasn't re-run.

## Step 5 — result

Twelve resources across `templates/{pokeproxy,mock-downstream,redis}/`: a Deployment + Service per workload, plus `pokeproxy-env`/`pokeproxy-rules` ConfigMaps. This is where the rest of H6 actually closes and where H7's cluster-side half lands.

**Rules become genuinely cluster-internal, not just relocated.** `values.yaml`'s `components.pokeproxy.rules` holds only `reason` + `match` per rule — no `url`. `configmap-rules.yaml` computes the downstream URL once, from the mock Service's own naming helper and `.Release.Namespace`, and merges it into each rule before `toJson`. The URL can't drift from the Service that actually exists because it's derived from the same helper that names the Service, not typed twice. Verified beyond "renders plausible YAML": piped the rendered `rules.json` through the real `pokeproxy.rules.load_rules()` and confirmed it parses into the identical three `Rule` objects the local `config/rules.json` produces, with the URL swapped to `http://pokeproxy-mock-downstream.pokeproxy.svc.cluster.local.:8001/pokemon`.

**A Go-json quirk worth knowing about, not a bug:** Sprig's `toJson` HTML-escapes `<`/`>` (`\u003c`/`\u003e`) — a Go `encoding/json` default aimed at safely embedding JSON in HTML, irrelevant here. `json.loads` decodes it back identically, so it was never a functional problem, but `kubectl describe configmap` would have shown garbled escapes for every `>`/`<` in `rules.json` (three of the four match conditions use one). Piped through `replace "\u003c" "<" | replace "\u003e" ">"` — the double-escaped `\u003c` in the template source is deliberate: Go's own string-literal parser would otherwise decode `"\u003c"` back into a literal `<` before the template even runs, matching nothing in the actual rendered text.

**`checksum/config-{env,rules}` pod-template annotations close the H1 consequence** (a rules ConfigMap edit was previously silently inert until a manual restart). Verified with three renders: changing a rule's match condition changes `checksum/config-rules` and leaves `checksum/config-env` untouched; changing an unrelated redis-only value leaves both pokeproxy checksums untouched. Since the checksum lives in `spec.template.metadata.annotations`, a changed value changes the pod template hash, which is exactly what makes a Deployment roll on `helm upgrade` — this is the standard checksum-annotation pattern, not a custom mechanism.

**`lifecycle.preStop.sleep.seconds: 5` on every workload closes H7's cluster-side half** — the app-side drain (Part 1, 112ms measured) was already correct; this is the "endpoint deregistration is async with SIGTERM" gap the planning doc flagged as cluster-side scope.

**Redis runs as uid 999 / gid 1000, not a guessed 999:999.** Checked the real `redis:7-alpine` image before writing the securityContext: `id redis` inside the image reports `uid=999(redis) gid=1000(redis)`, and `/data` is baked in owned by `redis:redis` (999:1000) — the group is 1000, not 999. Guessing 999:999 here would have produced a permission-denied crash loop against a real `emptyDir` the first time it needed to write. `fsGroup: 1000` at the pod level is what actually makes the `emptyDir` writable by that GID at mount time — `runAsUser`/`runAsGroup` alone control who the process runs as, not who owns the volume. Verified beyond reading the image metadata: ran `redis:7-alpine` in Docker as `--user 999:1000 --read-only` against a volume pre-chowned to `999:1000`, with the exact args the chart uses — `PONG`, a real `SET`/`GET` round-trip, `maxmemory` reporting exactly 134217728 bytes (128MB), `maxmemory-policy` reporting `allkeys-lru`, and a clean startup log with no permission errors.

**Image tags default to `CHANGEME`, deliberately, not a fallback like `.Chart.AppVersion`.** The design already committed to immutable git-sha tags (`pokeproxy:<sha>`) for the two images this project builds — a plausible-looking fallback (chart version, `latest`) would silently deploy the wrong thing, or nothing, if an operator forgets `--set image.tag=$(git rev-parse --short HEAD)`. `CHANGEME` fails in an immediately legible way (`ErrImagePull` naming a tag nobody could mistake for real) rather than a quiet wrong-version deploy. `redis`'s tag is a real, meaningful default (`7-alpine`) — it's a pinned upstream version, not a locally-built artifact, so there's nothing to forget.

**The HMAC secret contract for step 7:** `envFrom.secretRef.name: pokeproxy-hmac` on the pokeproxy container. Step 7's SealedSecret must decrypt into a plain `Secret` in the `pokeproxy` namespace named exactly `pokeproxy-hmac`, with one data key literally `POKEPROXY_HMAC_KEY` (uppercase — `envFrom` injects each Secret data key verbatim as the env var name). Not marking it `optional: true`: a missing Secret should leave pods in `CreateContainerConfigError`, which is the intended fail-fast, not a silently-started proxy with no HMAC key.

Verified by execution:

| Check | Result |
|---|---|
| `helm lint . --strict` | Clean |
| `helm template` (fake image tags via `--set`) | Renders 12 resources, zero errors |
| Rendered `rules.json` → `pokeproxy.rules.load_rules()` | Parses into the same 3 rules as local `config/rules.json`, URL correctly swapped to the cluster-internal mock Service |
| `checksum/config-{env,rules}` reacts only to its own ConfigMap's content | Confirmed with 3 renders (baseline / rule changed / unrelated redis value changed) |
| Service `spec.selector` vs. each Deployment's pod-template labels | Cross-checked programmatically — every Service matches exactly one Deployment, no over- or under-match |
| `serviceAccountName` on every Deployment | Resolves to a ServiceAccount actually rendered by the chart |
| Redis uid 999 / gid 1000, `--read-only`, `fsGroup`-writable `emptyDir` | Live container: `PONG`, `SET`/`GET` round-trip, correct `maxmemory`/`maxmemory-policy`, clean startup log |

**Not yet verified — genuinely needs a cluster:** whether the HTTP/exec probes pass against real running pods, whether the checksum annotation actually triggers a rollout on a live `helm upgrade` (only the rendered-value mechanics are confirmed here), and the pokeproxy→redis / pokeproxy→mock-downstream cluster-DNS resolution the ConfigMap's URLs assume. All three are step 6.

No Python changed — chart-only step, aside from using the app's own `load_rules()` as a verification tool (not modifying it).

## Step 5 follow-up (user review)

Three review comments, addressed in place rather than as a new numbered step:

1. **Probe properties made configurable via `.Values`, all three workloads.** Each component's `values.yaml` now carries a `probes` block (`startup` for pokeproxy only, `liveness`/`readiness` for all three — `path` where the probe is HTTP, `periodSeconds`/`timeoutSeconds`/`failureThreshold` everywhere). Redis's `exec: ["redis-cli", "ping"]` command itself stays hardcoded — that's the check's identity, not a tunable knob; only its timing is parameterized, consistent with what "properties" meant for the other two. Verified the default render is byte-identical to the previous hardcoded values (no behavioral drift from this refactor), then confirmed two independent overrides (`components.pokeproxy.probes.liveness.periodSeconds=20`, `components.redis.probes.readiness.failureThreshold=9`) land in exactly their own resource and nowhere else.

2. **Why `mock-downstream` is `Recreate`, not `RollingUpdate`:** `mock_service/main.py:9` keeps `received_pokemon` as a plain in-process Python list — no Redis, no shared store. Under `RollingUpdate`, two pods briefly coexist behind one Service, each with its own independent list; a `POST /pokemon` landing on pod A followed by `GET /received` landing on pod B would see an empty list. `Recreate` guarantees exactly one pod (and one list) exists at any moment. This is also the reason Part 3's post-deploy E2E check (post through the proxy, then read `/received`) is only deterministic against a single-replica mock.

3. **Rules file path, re-verified on request, not changed:** Dockerfile's `POKEPROXY_CONFIG=/etc/pokeproxy/rules.json` → ConfigMap key `rules.json` → volume `rules` (no `items:` override, so every key mounts) → volumeMount at directory `/etc/pokeproxy` (no `subPath`) → resulting in-container file `/etc/pokeproxy/rules.json`. Exact match, already proven functionally in step 5 by piping the rendered content through the app's real `load_rules()`. Nothing changed here.

Verified by execution:

| Check | Result |
|---|---|
| `helm lint . --strict` | Clean |
| Default `helm template` probe output | Identical to pre-change hardcoded values across all 3 workloads |
| `--set components.pokeproxy.probes.liveness.periodSeconds=20` | Lands only on pokeproxy's `livenessProbe`, nothing else moved |
| `--set components.redis.probes.readiness.failureThreshold=9` | Lands only on redis's `readinessProbe`, nothing else moved |

## Step 6 — result

First deployment onto a real cluster. Tooling installed in WSL from each project's own official release channel, pinned rather than "latest": `kubectl` v1.30.5 (from `dl.k8s.io`, matching the Windows client already in use) and `k3d` v5.9.0 (from `k3d-io/k3d`'s GitHub Releases binary). New `deploy/k3d/cluster.yaml` pins `image: rancher/k3s:v1.35.5-k3s1` — k3d's own current default — rather than letting the cluster silently pick up whatever k3d's default becomes later, consistent with every other image pin in this project. Single server node, zero agents: the leanest topology for the measured 7.62 GiB / 8 vCPU Docker Desktop VM budget (confirmed via `docker info` — identical to the WSL `Ubuntu` distro's own figures, since Docker Desktop's WSL2 backend shares that same resource pool, not a separate allocation).

**A real design bug, found by actually running it, not a hypothetical.** Step 4 flagged as "not yet verified" that the chart's own `Namespace` resource plus `helm install -n pokeproxy` (no `--create-namespace`) was a known-working pattern. It isn't, for this Helm version: the very first install failed outright, `Error: create: failed to create: namespaces "pokeproxy" not found` — Helm requires the target namespace to exist before applying anything, regardless of resource-kind ordering. Adding `--create-namespace` doesn't fix it either — that flag creates the namespace via an untracked raw API call, and the chart's own Namespace resource then collides with Helm's ownership-metadata check: `Error: 1 error occurred: * namespaces "pokeproxy" already exists`. Both failure modes reproduced and captured verbatim before touching anything, per "diagnose the actual error before changing code."

**Fix:** removed `templates/namespace.yaml` from the chart entirely. The namespace (with PSA + ownership labels) is now created declaratively *outside* the Helm release:
```bash
kubectl create namespace pokeproxy --dry-run=client -o yaml | \
  kubectl label --local -f - \
    pod-security.kubernetes.io/enforce=restricted \
    pod-security.kubernetes.io/audit=restricted \
    pod-security.kubernetes.io/warn=restricted \
    app.kubernetes.io/part-of=pokeproxy \
    app.kubernetes.io/managed-by=Helm \
    -o yaml | kubectl apply -f -
```
`kubectl apply` on a locally-rendered manifest is idempotent and declarative in spirit even though it isn't Helm-tracked — this is exactly what Part 5's bootstrap script will run as a step before `helm upgrade --install`, so nothing here is a one-off hand fix. `helm lint --strict` and `helm template` both stayed clean after the removal.

**A second real bug, also found only by running it.** After the first successful `helm upgrade --install` (redis and mock-downstream healthy, pokeproxy correctly blocked on the not-yet-created HMAC secret — see below), `kubectl describe` on the mock-downstream pod showed: `Killing ... Container mock-downstream failed liveness probe, will be restarted`, plus 16 readiness-probe connection-refused events in the first 70 seconds. Step 5's plan deliberately gave `mock-downstream` and `redis` no `startupProbe`, reasoning "fast native startup, no concern the way pokeproxy's protobuf/pydantic import chain is." That reasoning didn't hold under a real cold start on a freshly-imported image — a `livenessProbe` with the default `initialDelaySeconds: 0` started counting failures before the container had a real chance to bind its port, and killed it once. **Fixed:** added the same `startupProbe` pattern already built for pokeproxy (`probes.startup.{periodSeconds,failureThreshold}` in `values.yaml`) to `mock-downstream` and `redis` too. Re-verified with a fresh `helm upgrade --install --wait --timeout 3m`, which this time succeeded on the first attempt with **zero restarts** across all four pods.

**Temporary HMAC secret — deliberately not committed anywhere.** Step 7 (Sealed Secrets) doesn't exist yet, so `envFrom.secretRef: pokeproxy-hmac` on the pokeproxy container had nothing to reference. Created by hand, once, as a plain `kubectl create secret generic pokeproxy-hmac -n pokeproxy --from-literal=POKEPROXY_HMAC_KEY=<the documented .env.example dev key>` — same name, same key, same value the rest of the project already uses, so step 7 replaces *how* this secret is provisioned without changing what anything downstream expects. Not a script, not a chart feature, not committed — a manual, interactive, session-local bootstrap action, exactly as scoped when the contract was defined in step 5.

Verified by execution:

| Check | Result |
|---|---|
| `k3d cluster create` | 87s, single node Ready, all `kube-system` pods (coredns, metrics-server, local-path-provisioner, Traefik) healthy |
| `k3d image import` | Both `pokeproxy:<sha>` and `mock-downstream:<sha>` imported, 68s |
| `helm upgrade --install --wait --timeout 3m` (after both fixes) | Succeeds; all 4 pods `1/1 Running`, **0 restarts** |
| DNS from inside a pokeproxy pod | `pokeproxy-redis.pokeproxy.svc.cluster.local` and `pokeproxy-mock-downstream.pokeproxy.svc.cluster.local` resolve to the exact ClusterIPs `kubectl get svc` reports |
| Signed request through `kubectl port-forward` → `/stream` | `200 {"status":"received"}` — HMAC verified, Redis cache checked, protobuf decoded, rule matched, forwarded over cluster DNS |
| Same request confirmed in mock-downstream | `GET /received` shows the exact payload, correct `reason: "strong fire pokemon"` — proves rule matching end-to-end, not just a 200 |
| Repeat of the identical payload | `200 {"status":"received"}` again (replayed from cache, per M4's design) and `/stats` reports `duplicate_suppressed: 1` — proves Redis GET/SET actually round-trips over the cluster network, not just that the connection didn't error |
| `kubectl top pods` | Works (metrics-server functional) — idle-state figures only (pokeproxy ~2m CPU/47Mi, redis ~20m/7Mi), **not** the step 9 load measurement, noted here only as a readiness check for that later step |
| `helm history pokeproxy` | Revision 1 (superseded) and 2 (deployed) both present — the rollback target Part 3 will use already exists as a side effect |

**Repo layout note, minor deviation from the original sketch:** `values-local.yaml` was **not** created this step. Every value it would have held right now (the two image tags) is dynamic per-build, so it's supplied via `--set components.<name>.image.tag=$(git rev-parse --short HEAD)` at deploy time rather than a file that would need editing on every commit. `values-local.yaml` gets created in step 7, when the sealed HMAC ciphertext is the first genuinely static local-only value.

**Not yet verified — explicitly out of step 6's scope:** Traefik/ingress (step 8, not wired to anything yet — it's running idle, harmlessly, as a k3d default), whether `checksum/config` triggers a live rollout on a real `helm upgrade` (step 9), and real load-based resource measurement (step 9). The HMAC secret existing only as a manual `kubectl create secret` — not reproducible from a clean clone yet — is exactly what step 7 closes.

No Python changed. `app/` was used only as a verification client (building signed protobuf requests against the real proto module) — nothing in it was modified.

## Step 7 — result

Replaced step 6's manual `kubectl create secret` with the real Sealed Secrets flow, and proved the design's central claim by actually tearing down and recreating the cluster — not just asserting it would work.

**Controller:** chart `sealed-secrets/sealed-secrets` v2.19.3 (chart's own latest at time of writing) from `https://bitnami.github.io/sealed-secrets`, `--set image.registry=ghcr.io` (confirmed both `ghcr.io/bitnami/sealed-secrets-controller:0.39.1` and the `docker.io` equivalent exist before committing to either — picked `ghcr.io` per the design's reasoning to sidestep the Bitnami catalog migration), `fullnameOverride=sealed-secrets-controller` (the chart names itself `sealed-secrets`; `kubeseal`'s own default `--controller-name` expects `sealed-secrets-controller`), `keyrenewperiod="0"` to keep sealing deterministic against one pinned key. `kubeseal` CLI installed pinned to the exact matching version, `v0.39.1`, from the project's own GitHub Releases — client/server version parity matters for this tool specifically, since the wire format can evolve between versions.

**Sealing key, generated once via the documented "bring your own certificate" recipe** (`openssl req -x509 -newkey rsa:4096 ...`, wrapped as a `kubernetes.io/tls` Secret named `sealed-secrets-key` in `kube-system`, labeled `sealedsecrets.bitnami.com/sealed-secrets-key: active`), saved as a complete, self-sufficient manifest at `.secrets/sealing-key.yaml` — gitignored (new root `.gitignore`), but persists across `k3d cluster delete && k3d cluster create` on this machine, which is exactly what makes the design work: the controller adopts whatever key is labeled active in its own namespace at startup, so reusing the same local file means the same key every time, without ever putting the private key in git.

**`kubeseal --raw --from-file=POKEPROXY_HMAC_KEY=<value-file>` outputs a bare ciphertext string** (confirmed by running it, not assumed from docs) — exactly what a `SealedSecret`'s `spec.encryptedData.<KEY>` field needs. New `templates/pokeproxy/sealedsecret-hmac.yaml` templates the `SealedSecret` CRD from `.Values.hmac.encryptedValue`; new `values-local.yaml` holds the real ciphertext (first genuinely static local-only value for this file, as flagged back in step 6). `values.yaml`'s base default is `hmac.encryptedValue: CHANGEME` — same fail-loud-not-silently-wrong pattern already used for the two image tags: an un-sealed deployment gets an immediately diagnosable "this isn't valid ciphertext" failure from the controller, not a blank credential.

**New `scripts/seal-hmac.sh`** — the actual step-7 deliverable, not just the manual commands used to explore. Idempotent: generates the sealing key only if `.secrets/sealing-key.yaml` is absent, always re-applies it and re-installs the controller (both safe no-ops when already correct), and only reseals the HMAC value if `values-local.yaml` is missing or still holds the `CHANGEME` placeholder. Ran it twice against the *same* already-provisioned cluster first to confirm the short-circuit path is a true no-op before trusting it for the real test.

**HMAC value sealed is the existing documented dev secret** (`.env.example`'s value), not a freshly random one — consistent with L1's already-accepted reasoning (`docs/issues/000-known-gaps.md`) that a shared local-dev secret is a documented convenience, not a gap. `scripts/load_generator.py --secret` and every manual verification script in this project continue to work unmodified against this cluster. Overridable via `POKEPROXY_HMAC_KEY` env var for a real (non-dev) deployment reusing this same script pattern.

**The actual verification, run for real:**
1. `k3d cluster delete pokeproxy` — the cluster from step 6, gone entirely.
2. `k3d cluster create --config deploy/k3d/cluster.yaml` — 33s, fresh node.
3. `bash scripts/seal-hmac.sh` — reused the *existing* `.secrets/sealing-key.yaml`, applied it, installed the controller. Controller log on the brand-new cluster: `"registered private key" secretname=sealed-secrets-key` — **no** "generated new key" line. Script correctly found `values-local.yaml` already had a real value and left it untouched.
4. Namespace + PSA labels, `k3d image import`, `helm upgrade --install -f values-local.yaml --wait --timeout 3m` — **succeeded on the first try**, all 4 pods `1/1 Running`, zero manual secret creation this time.
5. Signed request through a fresh `kubectl port-forward`: a legendary Pokémon (Articuno) → `200 {"status":"received"}` — the exact same committed ciphertext, decrypted by a controller that had never seen this specific cluster before, produced a working HMAC key that verified a real signature.

The resulting `Secret pokeproxy-hmac` carries a proper `ownerReference` to the `SealedSecret` CR (`controller: true`), confirmed via `kubectl get secret ... -o jsonpath`. The step-6 manual secret was deleted before this flow ran, so there was no ownership collision to work around — unlike the Namespace situation in step 6, the `SealedSecret` CR is a resource the controller reconciles independently, not something Helm and another actor both try to own.

**One process hiccup, not a design bug:** the first deploy attempt this step failed with `ImagePullBackOff` — `pull access denied` for `pokeproxy:146c88a`. Root cause: I'd committed step 6 between sessions, so `git rev-parse --short HEAD` now returned a sha that was never built or `k3d image import`ed (only the prior `da102ba` had been). Rebuilt both images at the current sha, reimported, retried — succeeded. A real reminder of why CI (Part 3) always builds and imports at the exact sha it's about to deploy, never relies on a stale local image cache.

Verified by execution:

| Check | Result |
|---|---|
| `helm lint . --strict` (with `values-local.yaml`) | Clean |
| Controller adopts the pinned key, both before and after cluster recreation | Confirmed via controller log, both times: `registered private key`, never `generated new key` |
| `values-local.yaml` ciphertext | Byte-identical before and after the delete/recreate cycle — re-running the bootstrap doesn't churn git |
| `helm upgrade --install --wait --timeout 3m` on the fresh cluster | Succeeds first try, 0 manual steps, 0 restarts |
| Signed request end-to-end on the recreated cluster | `200 {"status":"received"}` for a matching payload; `200 {}` for a non-matching one (proves signature verification passed either way — a bad key fails with 401, not 200) |
| `kubectl get secret pokeproxy-hmac -o jsonpath='{.metadata.ownerReferences}'` | Owned by the `SealedSecret` CR, `controller: true` |

**Not yet verified — explicitly out of scope here:** whether `seal-hmac.sh` correctly handles a *genuinely* fresh clone (no `.secrets/` at all) — that path generates a new key and reseals, which is the documented, accepted trade-off, but wasn't exercised this session since `.secrets/` already existed throughout. `values-prod.yaml`'s equivalent secret story (step 10) is intentionally out of scope — no production cluster exists to seal against.

No Python changed.
