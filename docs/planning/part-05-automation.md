# Part 5 — Automation: Zero to Running in One Command

## What I am actually solving

`scripts/deploy.sh` already does most of what Part 5 asks: prerequisite checks, cluster create-or-reuse, image build at the exact HEAD sha, PSA namespace, monitoring stack, `helm upgrade --install --atomic` with the E2E hook as a real gate, and an external ingress probe. Three things stop it being "zero to running on a clean machine":

1. **A clean clone cannot run it at all.** Step 4 calls `seal-hmac.sh --env local`, which exits 1 when `.secrets/sealing-key-local.yaml` is missing — and `.secrets/` is gitignored, so on a fresh clone it is *always* missing.
2. **There is no teardown in code.** `k3d cluster delete pokeproxy` exists only as prose in `deploy/README.md`.
3. **There is no single entry point.** Dev, prod and key-provisioning are three scripts whose ordering lives only in a README, and there is still no root `README.md` (deliverable 8).

Part 5 closes those three and adds a debugging surface the project has never had: **one command stands the whole thing up; Tilt is what I use to test and debug it once it's up.**

## The chosen design, stated plainly

**`make up` is the deliverable. Tilt is the engine underneath it and the debugging UI on top of it.**

```
make up    →  preflight  →  k3d cluster create (if absent)  →  tilt ci
make dev   →  preflight  →  k3d cluster create (if absent)  →  tilt up      (interactive: UI, live-reload, buttons)
make down  →  tilt down  →  k3d cluster delete
```

`tilt ci` is a one-shot, non-interactive run: it builds, deploys, waits for every resource to be genuinely ready, and exits non-zero if anything fails or never converges. That is the CI-runner and clean-machine path. `tilt up` is the same graph in watch mode with the web UI — that is the human path.

**Tilt cannot create or delete the cluster.** It requires a working kube context and errors at startup without one. So a thin wrapper owns cluster lifecycle and Tilt owns everything inside the cluster. This is a structural fact about Tilt, not a stylistic choice, and it is why `make` is the entry point rather than `tilt` itself.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Tilt as the in-cluster orchestrator**, wrapped by a Makefile that owns cluster lifecycle | Gives a declared dependency DAG, per-workload readiness/logs/status, live-reload and custom action buttons — none of which a linear bash script can offer. The wrapper exists because Tilt is not a cluster provisioner |
| D2 | **`ext://helm_resource`, not Tilt's built-in `helm()`** | Built-in `helm()` runs `helm template`: no release, no hooks, no rollback ([tilt#3658](https://github.com/tilt-dev/tilt/issues/3658)). `helm_resource` runs a real `helm upgrade --install` and appends `flags` verbatim, so `--atomic`, `--timeout`, `-f` and `--set` all pass through unchanged. **This is what preserves Part 3's E2E-as-a-gate.** Verified by reading the extension's own `helm-apply-helper.py`, not assumed |
| D3 | **Model Kubernetes resources as Tilt resources**, not `deploy.sh`'s steps | `helm_resource` splits the chart into `pokeproxy` / `redis` / `mock-downstream`, each with its own status, logs, port-forward and live-update target. Wrapping the whole script in one `local_resource` would give one opaque box and none of the value. Only genuinely host-side one-shot prerequisites stay `local_resource` |
| D4 | **`make up` runs `tilt ci`, not `tilt up`** | The assignment's one command must terminate with a meaningful exit code. `tilt up` never exits. `tilt ci` exits 0 only when every resource is healthy |
| D5 | **Local sealing key: mint if absent, and re-seal whenever we minted it** | Sealing is RSA — a freshly minted keypair cannot decrypt ciphertext produced by my keypair's public half. That is why the mismatch exists at all. `minted_this_run == true → re-seal` removes it entirely, with zero manual steps. Safe for local specifically because Helm reads the **working tree** |
| D6 | **Prod keeps its one manual key step, unchanged** | Argo CD reads **git**, not the working tree, so automating prod means committing and pushing to `main` on every bootstrap — evaluated and rejected in `docs/issues/023`. Prod is not on Part 5's headline path; `up-prod` stays a secondary target |
| D7 | **k3d built-in local registry replaces `k3d image import`** | Tilt does not auto-import into k3d; its docs recommend the registry. Also a net speed win — today's script re-imports three full images on every run, the slowest step in `deploy.sh` |
| D8 | **`deploy.sh` is decomposed and deleted, not kept in parallel** | Its logic moves into the Tiltfile and the wrapper. Two deploy paths that can disagree is exactly the dead weight `CLAUDE.md` says to remove with the change that supersedes it |
| D9 | **Prod stays Argo CD; Tilt is dev-only** | Dev loop and delivery mechanism are different problems. Stated rather than fixed — the two share the Helm chart, which is the part that matters |

### Rejected alternatives

| Option | Why not |
|---|---|
| Pure shell, no Tilt | Works, and is what exists today. No DAG, no per-workload status, no debugging surface, no live-reload. The whole reason to add Tilt is the operability surface |
| Logic inside Makefile recipes | No `set -euo pipefail`, per-line shells, tab semantics. Ruled out on sight |
| Small Python/Go CLI | A runtime, packaging and its own tests, to wrap subprocess calls to docker/k3d/helm. Solves nothing this project has |
| Tilt as a thin `local_resource` wrapper around `deploy.sh` | One opaque box in the UI. Tilt only shows what it deployed, so this buys a progress bar and costs a dependency |
| Built-in `helm()` | `helm template` — loses the release, the hooks and `--atomic`. Would have turned Part 3's verification gate back into a report |

## Tilt resource graph

**As designed.** Four rows did not survive implementation; the as-built graph follows this one.

| Resource | Type | Depends on |
|---|---|---|
| `sealing-key` | `local_resource` — mint if absent (D5) | — |
| `sealed-secrets` | `local_resource` — install controller, pin key, re-seal if minted | `sealing-key` |
| `namespace` | `k8s_yaml` — PSA `restricted` labels | — |
| `monitoring` | `local_resource` — `kube-prometheus-stack` | `namespace` |
| `pokeproxy` / `mock-downstream` / `pokeproxy-e2e` images | `docker_build(only=[...], live_update=[sync('src/')])` | — |
| **`pokeproxy`, `redis`, `mock-downstream`** | **`helm_resource`** → real workloads, one Tilt resource each | `sealed-secrets`, `namespace`, `monitoring` |
| `e2e` | `local_resource`, `auto_init=False` — on-demand re-run | app ready |
| `ingress-probe` | `local_resource` — the 401 check | `e2e` |

### As built — what `Tiltfile` actually contains

| Resource | Type | Depends on | Delta from the design above |
|---|---|---|---|
| `sealing-key` | `local_resource` — `seal-hmac.sh --env local`: mint if absent, pin key, install controller, reseal if minted | — | **Merged with `sealed-secrets`** (step 3): one script already does all four in one pass |
| `namespace` | `k8s_yaml` — PSA `restricted` labels | — | unchanged |
| `monitoring` | `local_resource` — `install-monitoring.sh` | `namespace` | unchanged |
| images ×3 | `docker_build(only=[...])`, **no `live_update`** | — | **`live_update` dropped** (step 4): both Deployments hardcode `readOnlyRootFilesystem: true`, so no sync target is writable. Full rebuild + `--atomic` redeploy instead |
| `pokeproxy-helm` | `helm_resource` — **one** consolidated resource for the whole release | `sealing-key`, `namespace`, `monitoring` | **No native per-workload split** (step 1): `helm_resource` wraps `k8s_custom_deploy`, whose object list isn't known at Tiltfile-load time, so `k8s_resource(objects=…)` errors |
| `pokeproxy` / `redis` / `mock-downstream` | `local_resource` — `kubectl rollout status` + `serve_cmd: kubectl logs -f`, read-only | `pokeproxy-helm` | **New**: status/log shims standing in for the split above. Never a second applier of the chart's objects |
| `e2e` | `local_resource`, `auto_init=False` | `pokeproxy-helm` | unchanged in spirit |
| `ingress-probe` | `local_resource` — the 401 check, **auto, not on-demand** | `pokeproxy-helm` | **Depends on `pokeproxy-helm`, not `e2e`** (step 6): an auto resource behind an `auto_init=False` one sits in `waiting-for-dep` forever and `tilt ci` hangs |

`monitoring` must precede the chart so the Prometheus Operator CRDs exist when the chart's `ServiceMonitor`/`PrometheusRule` are applied. Today that ordering is a comment in `deploy.sh`; here it is an enforced edge.

```python
helm_resource('pokeproxy', 'deploy/helm/pokeproxy',
  flags=['-f', 'deploy/envs/local/values.yaml',
         '--atomic', '--timeout=3m', '--set', 'e2e.enabled=true'],
  image_deps=[...], image_keys=[...],
  resource_deps=['sealed-secrets', 'namespace', 'monitoring'])
```

### Debug and test surface (`tilt up`)

Custom buttons via `ext://uibutton`; inputs arrive as environment variables.

| Button | Runs |
|---|---|
| Send signed traffic | `load_generator.py`, `text_input` for rps and duration |
| Run E2E now | the real `pokeproxy-e2e` image as a one-off |
| Flush Redis cache | `redis-cli FLUSHALL` — the dedup/poisoned-cache lever from the Part 3 rollback story |
| Break / restore `rules.json` | reproduces Part 3 scenario B on demand |

This is the part Tilt earns outright: the Part 3 rollback scenarios and the load generator become one-click, and live-reload gives a sub-second code→pod loop that the current build-and-import cycle cannot.

## Failure model

| Class | Caught at | Behavior |
|---|---|---|
| Missing tool / version too old / Docker not running / port 8080 bound | preflight, before any state exists | exit 1 naming the tool **and** the fix; nothing created |
| Cluster creation fails | wrapper | exit 1 with k3d's own error |
| Sealing key absent | `sealing-key`, auto-minted and re-sealed | **not a failure — this is the automated path** |
| Image build fails | `docker_build` | red resource, `tilt ci` non-zero; cluster left intact |
| Monitoring never ready | `monitoring` | red resource, non-zero |
| App unhealthy **or E2E hook fails** | `helm_resource` | `--atomic` rolls the release back to the last good revision (uninstalls on first install), non-zero |
| Ingress unreachable | `ingress-probe` | non-zero with the actual status code |

**The cluster is never auto-deleted on failure.** A bootstrap that destroys its own evidence is unusable for debugging — which is precisely what Part 4 exists to support. Teardown is always explicit.

## Idempotency

Second `make up`: cluster reused → images rebuilt from layer cache at the same sha → namespace `unchanged` → seal no-ops (key already on disk, so nothing was minted, so nothing is re-sealed) → Helm revision N+1 → E2E hook re-runs safely, because `e2e_check.py:44` mints a `uuid4` name per run so the dedup cache never poisons it.

**Measured, and one half of this turned out to be wrong** (clean-machine audit below, A-2). The *app* half holds exactly as written: 2nd `make up` = exit 0 / 171s, pod `startTime` identical across both runs, `RESTARTS 0`, sealing key sha + mtime unchanged, `values.yaml` unchanged, `pokeproxy` release 1→2 `Upgrade complete`. The *monitoring* half does not: `helm upgrade` on `kube-prometheus-stack` regenerates Grafana's auto-generated admin password every run, which rolls the Grafana Deployment. `make up` is idempotent for what it deploys and near-idempotent for what it installs alongside it.

## Teardown

`make down` = `tilt down` (removes what Tilt deployed) then `k3d cluster delete pokeproxy`. `.secrets/` and the working tree are left alone. `make down-prod` deletes the prod stand-in cluster.

## Repository layout

As built (the design's original list plus six scripts the button/status surface turned out to need):

```
Makefile                              # new — up / dev / down / up-prod / down-prod / status / help
Tiltfile                              # new
README.md                             # new — deliverable 8
scripts/
  preflight.sh                        # new — commands + version floors, docker running, host port free
  up.sh  down.sh                      # new — cluster lifecycle + tilt invocation
  down-prod.sh                        # new — prod stand-in teardown (P5-3)
  status.sh                           # new — read-only cluster/pods/releases/ingress probe
  install-monitoring.sh               # new — kube-prometheus-stack, extracted from deploy.sh +
                                      #       bootstrap-prod.sh so local and prod share one path
  run-e2e-now.sh                      # DELETED in the final cleanup pass — ad-hoc E2E Job at the currently-deployed tag, superseded by the Helm post-install E2E hook
  break-rules.sh  restore-rules.sh    # new — Part 3 scenario B on demand, behind the Tilt buttons
  seal-hmac.sh                        # + mint-implies-reseal (D5)
  init-sealing-key.sh                 # unchanged
  bootstrap-prod.sh                   # + preflight; prod stays Argo CD, not Tilt
  deploy.sh                           # DELETED — superseded by Tiltfile + wrapper (D8)
deploy/envs/local/values.good-rules.yaml     # new — fixtures for the break/restore buttons
deploy/envs/local/values.broken-rules.yaml   # new
deploy/k3d/cluster.yaml               # + local registry (D7)
docs/issues/026-clean-clone-sealing-key-bootstrap.md   # new — P5-1 + P5-2
docs/issues/027-no-teardown-in-code.md                 # new — P5-3
docs/planning/part-05-automation.md   # this file
```

## Gaps this Part closes

| ID | Gap | Evidence | Sev |
|----|-----|----------|-----|
| P5-1 | Clean clone cannot run `deploy.sh` — `seal-hmac.sh` exits 1 with no key, and `.secrets/` is gitignored | `seal-hmac.sh:90-106`, `.gitignore:1` | Blocker |
| P5-2 | Provisioning the key first does not help: `already_sealed()` tests only "non-empty and ≠ CHANGEME", so the committed ciphertext passes and re-sealing is skipped → `CreateContainerConfigError` → `--atomic` rollback 3 min later with a Kubernetes-level symptom that never names secrets. This is **F-15, open since 2026-08-23** | `seal-hmac.sh:65-70`, `:127-130` | Blocker |
| P5-3 | No teardown in code; prod has none documented at all | `deploy/README.md:103-109` | Blocker |
| P5-4 | No single entry point — three scripts, ordering only in a README | repo | Should fix |
| P5-5 | No root `README.md` (deliverable 8) | `git ls-files` | Should fix |
| P5-6 | Preflight is command-presence only: no version floors, no host-port check. Port 8080 in use → `k3d cluster create` dies with a Docker port-bind error that never names the cause | `deploy.sh:26-43` | Should fix |
| P5-7 | Cold-start sizing: A-13 recorded the monitoring stack CPU-throttled with Prometheus already missing rule evaluations on a *warm* cluster. `--wait --timeout 5m` while a cold cluster pulls ~10 images is the tightest budget in the flow | `part-04-observability.md:213` | Nice to have |

## Implementation steps

| # | Step | Gate |
|---|---|---|
| 1 | Minimal Tiltfile proving the `helm_resource` core against the existing dev cluster | Real release in `helm list`; E2E hook executes after readiness; `--atomic` rolls back on a deliberately failed E2E; chart splits into per-workload Tilt resources |
| 2 | `scripts/preflight.sh`, wired into the wrapper and `bootstrap-prod.sh` | Runs clean; bogus `PATH` and occupied port 8080 both produce the intended message |
| 3 | P5-1 / P5-2 — mint-implies-reseal in `seal-hmac.sh` | Fresh-clone simulation: key moved aside, `make up` succeeds with no manual step |
| 4 | Full Tiltfile — all resources, k3d registry, `docker_build` with `only`/`live_update` | `tilt ci` green from a clean cluster |
| 5 | Buttons (`ext://uibutton`) | Each button verified by clicking it against a live stack |
| 6 | `Makefile` + `scripts/up.sh` / `down.sh`; delete `deploy.sh` | `make up` / `make down` / `make up` again |
| 7 | Root `README.md` (deliverable 8) + `deploy/README.md` rewrite | Every command in it actually run |
| 8 | Clean-machine verification, then docs | `make down` → `make up` → `make up`; issue write-ups for P5-1/P5-2 |

## Definition of done

| # | Item | Status |
|---|---|---|
| 1 | One command takes a clean supported machine to a fully running, monitored stack | **Done** — step 8, cold `make up` from no cluster + no sealing key: exit 0, `real 10m22.139s`, all workloads healthy, ingress `401` |
| 2 | That command is idempotent — safe to re-run | **Done** — step 8's 2nd `make up`: exit 0, cluster/containers reused (`Created` timestamps identical), `helm history` went 1→2 (`Upgrade complete`, not reinstall), 0 pod restarts, no spurious reseal |
| 3 | It fails loudly and clearly when a prerequisite is missing | **Done** — step 2, live-tested against a bogus `PATH` and a genuinely occupied port 8080, both name the exact cause and a fix |
| 4 | Teardown path exists in code | **Done** — step 6 (`make down`/`scripts/down.sh`), re-confirmed in step 8: exit 0, `docker ps -a`/`k3d cluster list` both empty afterward |
| 5 | Post-deploy functional verification still **gates** (E2E hook + `--atomic`) | **Done** — step 1 proved `helm_resource` runs real `helm upgrade --install` with `--atomic`, hook executes post-readiness, deliberate E2E failure triggers real rollback (`helm history` 8→9(rollback)→10). Step 8's two clean runs both show `REVISION: 1`/`2`, `STATUS: deployed` — no rollback needed because the hook genuinely passed both times |
| 6 | Root `README.md` explains layout and how the bootstrap works | **Done** — step 7, `README.md`, gate was "nearly every documented command actually run this session" |
| 7 | Verified from an actual clean-clone simulation, not reasoned about | **Done** — step 8: sealing key genuinely absent (moved aside, not just old), cluster genuinely absent (`make down` first), two full `make up` cycles plus `make status`/`make down`, all evidence read from live command output, not asserted |

## Open / unverified

- ~~`helm_resource` behavior against *our* chart is documented, not yet observed~~ — **resolved by step 1**: real `helm upgrade --install`, hooks intact, `--atomic` rollback confirmed against a deliberately broken E2E. Re-confirmed by step 8's two clean cold/idempotent runs, neither of which needed to roll back.
- **The E2E hook Job will not appear as a tracked Tilt resource.** `helm_resource` reports objects via `helm get manifest`, which excludes hooks by design. The gate still works — real Helm executes the hook and `--atomic` acts on it — but its output lands in the `helm_resource` log rather than its own tile. Mitigated by the separate on-demand `e2e` resource. Permanent characteristic of the extension, not an open item.
- ~~Part 4 is landed... step 8's verification should run against a fast-forwarded tree~~ — **resolved**: step 8 ran against the actual current working tree (local `main` at `7555fc0`, matching the branch this repo has been on throughout), no stale-monitoring-values concern surfaced.
- ~~`make` is not present in a minimal WSL Ubuntu~~ — **corrected by step 8**: it is present on this session's actual WSL Ubuntu install. The README's "equally supported" fallback documentation stands regardless, so nothing needed to change.

---

## Clean-machine audit — 2026-08-25

Independent pass, run after step 8 declared Part 5 complete. Rule: start from the documented prerequisites only, trace the bootstrap and teardown paths as an outsider would, and prefer executing over reasoning. No code was changed by this audit — findings only.

### What was actually executed

Starting state was genuinely cold: no k3d cluster, no containers, and `.secrets/sealing-key-local.yaml` moved aside so the mint path had to fire. All runs in WSL Ubuntu, the only environment where the toolchain exists on this box.

| Run | Result |
|---|---|
| Cold `make up` (no cluster, no sealing key) | **exit 0, 523s.** `sealing-key`: `No sealing key found` → minted → resealed (`values.yaml` sha `c0db6cd1…` → `88216305…`, a real diff, not just a log line). `pokeproxy` + `kube-prometheus-stack` both `REVISION: 1 / Install complete` — no `--atomic` rollback. `SUCCESS. All workloads are healthy.` |
| Post-deploy E2E gate | Real, not a report: `job/pokeproxy-e2e` → `SuccessfulCreate` → `Completed` inside the `helm upgrade` (Step 7 = 78.78s), before Helm reported success |
| Functional check (app) | 4/4 pods `Running`, `RESTARTS 0`; unsigned `POST /stream` → **401** through the host ingress |
| Functional check (**monitoring**, the part `make up` never asserts) | Prometheus: **14/14 active targets `up`, zero unhealthy**, including `serviceMonitor/pokeproxy/pokeproxy/0` ×2. Real app series ingested — `sum(pokeproxy_requests_total)=6` (the E2E hook's own traffic), `cache_operations_total=3`, `downstream_requests_total=1`, `build_info` on both replicas. All three `PokeProxy*` rules loaded, `health=ok`, `state=inactive`; 0 rules with `health!=ok`; Alertmanager discovered at `10.42.0.14:9093`. Grafana `/grafana/api/health` → **200** (`13.2.0`) through the ingress, `pokeproxy-overview.json` present in the sidecar's `/tmp/dashboards` |
| 2nd `make up` (idempotency) | **exit 0, 171s.** Cluster + registry containers reused (`Created` identical), pod `startTime` identical, `RESTARTS 0`, sealing key sha/mtime unchanged, `values.yaml` unchanged, both releases 1→2 `Upgrade complete` — a real upgrade, not a reinstall |
| `make status` | Real output: cluster `1/1`, 4/4 pods, 5 releases, `http_code=401` |
| `make down` | **exit 0, 15s.** `helm uninstall pokeproxy` then `k3d cluster delete`. Afterwards: zero clusters, zero registries, zero containers, zero `k3d-*` networks/volumes, kube context removed from kubeconfig. `.secrets/` untouched |

**The headline claim holds.** One command takes a genuinely cold machine to a running, *actually monitored*, functionally verified stack; a second run is safe; teardown is complete and leaves no cluster-side residue.

### Findings

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| A-1 | **Blocker** | **Part 5 does not exist in git.** Every artifact is untracked and the branch has no upstream, so a clean *clone* has no `Makefile`, no `Tiltfile`, no root `README.md`, none of the nine new scripts, no planning doc and no issue write-ups — it gets the pre-Part-5 `scripts/deploy.sh` instead. Everything verified above was verified against the working tree, which is the only place Part 5 exists | `git ls-tree --name-only HEAD scripts/` → 4 files (`bootstrap-prod.sh`, `deploy.sh`, `init-sealing-key.sh`, `seal-hmac.sh`); `git status -sb` → `## feature/part5-automation`, no upstream |
| A-2 | Should fix | **`make up` rolls Grafana on every re-run**, so the "idempotent" claim is weaker than stated for the monitoring half. `helm upgrade` regenerates the chart's auto-generated Grafana admin password (`grafana.adminPassword` unset → `randAlphaNum`), which changes the Secret, which changes the pod-template checksum. Consequences: an admin password saved from a previous run stops working, the dashboard drops during each re-run, and every `make up` pays a Grafana rollout | Grafana ReplicaSet `59c99777f4` → `6ddd96b8df`. Of the three pod-template checksums, `checksum/config` and `checksum/sc-dashboard-provider-config` are **byte-identical**; only `checksum/secret` differs (`a3145f72…` → `477acc31…`). Fix: pin `grafana.adminPassword` (or `admin.existingSecret`) in `deploy/monitoring/values.yaml` |
| A-3 | Should fix | **Preflight doesn't check host port 5000**, the k3d registry's `hostPort` (D7). P5-6 added the port check specifically so a bound port fails *before* anything is created and *names the cause* — port 5000 is exempt from that, and it is the one port this design newly introduced | Live, with 5000 held by a docker container: `preflight: OK`, exit 0 → `k3d cluster create` **FATAL 38s later**: `Bind for 0.0.0.0:5000 failed: port is already allocated`. k3d does roll back cleanly, so no partial state — but the error names neither the owner nor the fix. Same gap applies to 10350, Tilt's UI port |
| A-4 | Should fix | **`make up` never asserts that monitoring monitors anything.** The `monitoring` resource's success criterion is `helm upgrade --wait` — "the pods are ready", not "Prometheus is scraping the app, the rules loaded, Grafana serves". Today all of that genuinely works (table above), but nothing in `tilt ci` would catch a regression, and Part 4's own audit found exactly this class silently broken twice (A-2 Grafana target permanently down; two empty dashboard panels) | Fix is cheap: one `local_resource` after `pokeproxy-helm` asserting `up{job="pokeproxy"}==1` and the three rules present. Reachable through Grafana's datasource-proxy API on the existing ingress, so it needs no port-forward and doesn't reopen the Part 4 A-3 "Prometheus off the Ingress" decision |
| A-5 | Should fix | **`jq` is an undeclared prerequisite.** `scripts/run-e2e-now.sh` parses `helm get values -a -o json` with `jq` and dies without it. Not in `preflight.sh`, not in the README's prerequisites table. Off the `make up` path (the `e2e` resource is `auto_init=False`), but squarely on the `make dev` "Run E2E now" button path | `scripts/run-e2e-now.sh:10-16`; `preflight.sh` requires docker/kubectl/helm/k3d/kubeseal/git/tilt only |
| A-6 | Should fix | **Residual F-15, already documented and still open.** `already_sealed()` tests only "non-empty and ≠ CHANGEME", never decryptability. A sealing key that is *present but foreign* to the ciphertext in the working tree still skips the reseal → `CreateContainerConfigError` → `--atomic` rollback ~3 min later, with a symptom that never says "secret". The realistic trigger sits right next to the clean-machine story: a developer re-clones the repo and keeps their `.secrets/` | `seal-hmac.sh:65-70`, `:141-144`. Honestly scoped in `docs/issues/026` § Tradeoffs; recorded here so it isn't lost in the gap between "P5-2 closed" and "F-15 open" |
| A-7 | Nice to have | `default_registry('localhost:5000', host_from_cluster='k3d-pokeproxy-registry:5000')` is both **dead and wrong**. Tilt auto-detects the k3d registry and logs `Default registry specified, but will be ignored`; the real cluster-network host is `pokeproxy-registry:5000` — no `k3d-` prefix. Inert today, an unpullable-image trap if auto-detection ever misses | `Tiltfile:13`; Tilt log: `HostFromClusterNetwork:pokeproxy-registry:5000` |
| A-8 | Nice to have | `version_at_least()` never strips a leading `v`, so any tool reporting `vX.Y.Z` passes its floor unconditionally — digits sort before letters under `sort -V`. Only `kubeseal` is exposed (its extractor is the one that doesn't strip `v`); this box reports a bare `0.39.1` so the floor works here, but older kubeseal builds print `v0.x.y` and would sail through | `printf 'v0.9.0\n0.24.0\n' \| sort -V \| head -1` → `0.24.0` → "passes". One-line fix inside `version_at_least` |
| A-9 | Nice to have | `update_settings(k8s_upsert_timeout_secs=240)` bounds the whole `helm upgrade` process, while Helm's own worst case is readiness (`--timeout=3m`) **plus** the post-install hook (another 3m; the Job's own `activeDeadlineSeconds` is 180) inside that one process. Measured 78.78s cold here — 3× margin — but step 1's recorded `pending-upgrade` incident is this exact failure at the 30s default | Raise to ≥480s, or lower Helm's `--timeout`, so Tilt can never SIGKILL Helm outside `--atomic`'s control |
| A-10 | Nice to have | **Cold-start serialization.** Image builds are steps *of* `pokeproxy-helm`, which waits on `monitoring`, so ~5 minutes of monitoring image pulls elapse before the first `docker build` starts — even though the builds are independent. The real dependency is only the Prometheus Operator **CRDs**, not the whole `--wait` install | Cold-run log: `monitoring` starts at line 91, `STEP 1/7 — Building Dockerfile` not until line 122. Splitting a fast CRD-only dep from the slow install would overlap them |
| A-11 | Nice to have | `make down` leaves the built images behind, unbounded across cycles — 15 tags / ~3.5 GB after this audit (9 `localhost:5000/*:tilt-*` plus 6 older sha-tagged ones). Correct to keep for layer-cache reasons; worth documenting, or offering an opt-in prune | `docker images` after `make down` |
| A-12 | Nice to have | Stale references to the deleted `scripts/deploy.sh` survive in **live config**, not just historical docs | `deploy/monitoring/values.yaml:3`, `:111` |
| A-13 | Nice to have | Tilt's extensions (`ext://helm_resource`, `ext://uibutton`) are fetched **unpinned** from GitHub at Tiltfile-load time on any machine without a `~/.local/share/tilt-dev/tilt_modules` cache — an undocumented network and supply-chain dependency of the one command | Cold-run log: `python3 /home/idanb/.local/share/tilt-dev/tilt_modules/github.com/tilt-dev/tilt-extensions/helm_resource/helm-apply-helper.py` |

A-14 (drift, fixed by this audit rather than reported): this document's own "Tilt resource graph" and "Repository layout" sections still described the pre-implementation design — `live_update`, a separate `sealed-secrets` resource, a per-workload `helm_resource` split, and a scripts list missing six files that now exist. Corrected above; the as-designed tables are kept alongside the as-built ones, since the deltas are the interesting part.

### What the audit did not do

- Did not test a literal `git clone` — impossible while A-1 stands, and committing was not authorized.
- Did not re-run the prod path (`make up-prod` / `make down-prod`); no `pokeproxy-prod` cluster existed before or after, and step 7's evidence for it was not re-derived.
- Did not click the `make dev` buttons; step 5's evidence for those stands unre-verified by this pass.

### Fixes — 2026-08-25

A-2 through A-5 fixed and live-verified against the cluster already running from the audit above (not a fresh cold start — the fixes were exercised directly, then confirmed together in one full `tilt ci` run at the end). A-8/A-9/A-12 (three of the nice-to-haves) folded in as trivial one-line-or-comment fixes alongside them; A-1 (commit/push) is a go/no-go decision, not a code fix — resolved separately once this write-up lands. A-6/A-7 need no new action (A-6 is `docs/issues/026`'s already-scoped residual gap; A-7 is exactly what A-8's fix for `Tiltfile` also removes below). A-10/A-11/A-13 are deferred to `WORKLOG.md`'s Backlog — genuine design changes, not quick fixes, and none of them affect correctness.

| ID | Fix | Files | Verification |
|---|---|---|---|
| A-2 | `grafana.adminPassword` pinned to a fixed dev value in `deploy/monitoring/values.yaml`, replacing the subchart's `randAlphaNum` default that regenerated on every `helm upgrade` | `deploy/monitoring/values.yaml`, `deploy/README.md` (login line updated) | Ran `scripts/install-monitoring.sh` twice live: `checksum/secret` identical between runs, **no new Grafana ReplicaSet created** the second time (`kube-prometheus-stack-grafana-c478589d5` stayed the active RS both times, `deployment.kubernetes.io/revision` unchanged) |
| A-3 | `scripts/preflight.sh`'s host-port check extracted into `check_host_port_free()` and called for port 5000 (the k3d registry) and port 10350 (Tilt's UI) in addition to the existing 8080/8081 check | `scripts/preflight.sh` | Live: stopped `pokeproxy-registry`, occupied :5000 with a foreign container → `preflight --env local` correctly failed, naming port 5000 and `port5000hog` as the owner, before `k3d cluster create` would ever run. Confirmed the *legitimate*-owner path still passes (registry running normally → `OK`). Registry container restored afterward |
| A-4 | New `scripts/monitoring-health.sh`, wired as the `monitoring-health` `local_resource` (`resource_deps=['pokeproxy-helm']`) in the `Tiltfile`. Asserts, through Grafana's datasource-proxy (`/grafana/api/datasources/proxy/uid/prometheus/api/v1/...` — Grafana 13.2's proxy dropped the legacy numeric-id path used in some older docs, so this is the uid-based route, confirmed live) rather than a direct Prometheus route: `up{job="pokeproxy"}` shows as many `1`s as the Helm release's declared `replicaCount` (retried over 90s — one 15s scrape interval isn't enough margin), all three `PokeProxy*` rules `health=ok`, and Grafana's own `/api/health` returns 200 | `scripts/monitoring-health.sh` (new), `Tiltfile` | Live, three-part: (1) healthy stack → passes, 2/2 targets up, 3/3 rules ok; (2) `kubectl scale deployment pokeproxy --replicas=0` → correctly **fails** after the 90s window, `got 0` reported — this only works because expected-replica count is read from `helm get values` (the release's declared state), not from the live Deployment's own `.spec.replicas`, which the same `kubectl scale` also zeroes, which would make a naively-written check pass trivially against the exact failure it exists to catch; (3) scaled back to 2, rollout confirmed, re-ran → passes again. Then ran unmodified inside a full `tilt ci`: resource graph ordering correct (`pending` behind `pokeproxy-helm` until it completed), passed, run ended `SUCCESS. All workloads are healthy.`, exit 0. A fresh-context review pass (see below) caught that the script's first version hardcoded the Grafana admin password as a literal duplicate of `deploy/monitoring/values.yaml`'s `adminPassword` — fixed to read it from the `kube-prometheus-stack-grafana` Secret at runtime instead, re-verified live after the change. **Reversed later the same day** at direct user request — the `monitoring-health` Tilt resource was removed from the `Tiltfile` (no longer an automatic `tilt ci` gate). **`scripts/monitoring-health.sh` itself was then deleted in the final cleanup pass** — once its Tilt gate was gone it had no remaining caller, so keeping it as a dead standalone file was not worth the weight; see the addendum at the end of this document |
| A-5 | `require_tool jq` added to `preflight.sh`'s `local`-only tool block; `jq` row added to the root `README.md` prerequisites table | `scripts/preflight.sh`, `README.md` | Read-verified against `scripts/run-e2e-now.sh:10-16`'s actual `jq` usage; `preflight.sh --env local` still passes with `jq` present (this machine already has it). **Now a stronger fix than A-5 originally described**: A-4's `monitoring-health.sh` also uses `jq` directly, and unlike `run-e2e-now.sh` it's on the default `make up` path (no `auto_init=False`), which the full `tilt ci` run in A-4's own verification exercised for real — so this closes a hard `make up` dependency, not just the `make dev` button's. **Reverted in the final cleanup pass**: both `jq` callers (`monitoring-health.sh`, `run-e2e-now.sh`) were deleted, so the `require_tool jq` line and the README row went with them — see the addendum at the end of this document |
| A-8 | `version_at_least()` now strips a leading `v` from `$have` before the `sort -V` comparison | `scripts/preflight.sh` | Code-reviewed against the exact failure mode A-8 described (`vX.Y.Z` sorting before a bare floor); no tool on this box currently reports a `v`-prefixed version to exercise it live |
| A-9 | `update_settings(k8s_upsert_timeout_secs=...)` raised 240 → 480 | `Tiltfile` | Exercised for real by the full `tilt ci` run in A-4's verification — no timeout, well inside the new budget |
| A-12 | Both stale `scripts/deploy.sh` references in `deploy/monitoring/values.yaml` (lines 3, 111 in the audit's numbering) corrected to name `scripts/install-monitoring.sh` / the Tiltfile+`bootstrap-prod.sh` callers instead | `deploy/monitoring/values.yaml` | Read-verified — `grep -rn deploy\.sh deploy/monitoring/values.yaml` now empty |

**Review pass.** A fresh-context agent reviewed the full working-tree diff (static only, no live cluster access) against this write-up. No correctness bugs found in the shell/Tiltfile logic — it specifically traced `check_host_port_free()`'s three call sites, the `v`-strip's scope (only `$have`, never `$want`, correctly), and the replica-count-from-Helm-values approach against the chart's actual value structure. Confirmed the hardcoded dev Grafana password is disclosed accurately in `deploy/README.md` and isn't a real credential leak (the "prod" stand-in is itself a second local-only k3d cluster). Three doc-accuracy findings, all fixed: the root `README.md`'s "what `tilt ci` does" list didn't mention the new monitoring-assertion step (fixed); the Grafana admin password was duplicated as a literal in `monitoring-health.sh` rather than read from its actual source, the chart's own Secret — fixed as noted in A-4's row above; and the `jq` prerequisite row's justification hadn't been updated for A-4 making it a hard `make up` dependency rather than only a `make dev` button's (fixed, both here and in `README.md`).

A-7 closed as a side effect of A-9's edit: the dead, wrong `default_registry('localhost:5000', host_from_cluster='k3d-pokeproxy-registry:5000')` line is deleted from the `Tiltfile` (Tilt already ignored it and logged that it does; the hostname was also wrong — confirmed live in the audit as `pokeproxy-registry:5000`, no `k3d-` prefix), replaced with a comment explaining why no explicit registry setting is needed.

### Addendum — final cleanup pass, 2026-08-25

`scripts/monitoring-health.sh` and `scripts/run-e2e-now.sh` are both deleted from the repo, and the `jq` prerequisite (`preflight.sh`'s `require_tool jq`, the README prerequisites row) is reverted with them. Everything above describing those two scripts and the `jq` fix is accurate history of work done and verified live at the time — it is not the current state.

- `monitoring-health.sh`'s only caller was the `monitoring-health` Tilt resource, and that gate was already reversed at direct user request the same day it was added (A-4's fix row, above). Once removed from the `Tiltfile`, the script had no wiring left to anything — an orphaned manual-check file wasn't worth keeping.
- `run-e2e-now.sh` was an ad-hoc convenience for firing an E2E Job against whatever tag was currently deployed. The Helm post-install/PostSync E2E hook (`deploy/helm/pokeproxy/templates/e2e/job.yaml`, Part 3) already runs the same check as a real gate on every `make up` and every prod sync — the ad-hoc script offered no capability the hook didn't already cover on its own.
- Deleting both removed the last two `jq` callers in the repo. There is nothing left in the codebase that parses JSON with `jq`, so the prerequisite named in A-5 above is gone too.
