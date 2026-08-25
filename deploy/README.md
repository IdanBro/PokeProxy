# Deploying PokeProxy

Local dev is now automated end to end (Part 5) — see the root `README.md` for `make up`/`make dev`/`make down`. This file covers what's still manual: the prod stand-in (Argo CD), monitoring access details, and a few commands worth knowing for debugging. Every command below was run against a real cluster this session, with two deliberate exceptions noted where they appear: a one-time prod key-provisioning step that refuses to run twice by design, and the CI rollback dispatch, which is unchanged, already-evidenced Part 3 content this rewrite didn't touch — re-running either for this rewrite would have meant minting a second incompatible prod key or firing a real GitHub Actions rollback for no reason.

## Prerequisites

`scripts/preflight.sh` checks all of this for you (and is the first thing `make up`/`make dev`/`make up-prod` run) — command presence, version floors, Docker running, and the host port free. By hand, you need:

- Docker, running
- `kubectl`, `helm`, `k3d`, `kubeseal`, `git` on `PATH` (plus `tilt` for local — not needed for the prod stand-in, which stays script-driven)
- A POSIX shell (WSL on Windows — every command in this repo has only been verified there)

## Local

**`make up`** (or `make dev` for the interactive Tilt UI) is the actual bootstrap now — see the root `README.md` for the full explanation and the debug button surface. This section is what's underneath it, for when you want to touch a piece directly.

### What Tilt is actually doing

The `Tiltfile` at the repo root replaces the old `deploy.sh`: mint-or-reuse the local sealing key, install/reuse the sealed-secrets controller, install/reuse `kube-prometheus-stack`, build and push all three app images through a k3d-local registry, then `helm upgrade --install` the chart with `--atomic` so a failing post-install E2E check rolls the release back automatically. `docs/planning/part-05-automation.md` has the full resource graph and the reasoning behind each piece.

### Sealing key (local)

Unlike prod (below), the local sealing key is fully automatic: `scripts/seal-hmac.sh --env local` mints `.secrets/sealing-key-local.yaml` if it's missing and re-seals `deploy/envs/local/values.yaml` against it in the same run — safe specifically because Helm reads the working tree locally, not git. Nothing to do by hand; `make up`/`make dev` call this for you every run.

### Manual verification

```bash
curl -i -X POST http://localhost:8080/stream   # expect 401 — no signature on this plain request
```

Verified this session: `http_code=401`. `/stream` is POST-only — a bare `curl -i http://localhost:8080/stream` sends a GET and returns **405**, not 401.

For a real signed request, `app/scripts/load_generator.py` builds valid protobuf + HMAC payloads (this is also what the Tilt "Send signed traffic" button runs):

```bash
cd app && .venv/bin/python3 scripts/load_generator.py --url http://localhost:8080/stream --rps 5 --duration 4
```

Verified this session: `Done. Sent: 15, Errors: 0, Error rate: 0.0%` at `--rps 5 --duration 4`. Needs `app/.venv` populated (`uv sync` from `app/`, or your own venv with `httpx`+the generated `pokeproxy.proto` package) — a genuinely fresh clone won't have this yet; it's a dev-loop convenience, not part of the `make up` gate.

## Monitoring stack

`scripts/install-monitoring.sh` installs `kube-prometheus-stack` (Prometheus, Grafana, Alertmanager, kube-state-metrics, node-exporter, the Prometheus Operator) into a `monitoring` namespace, trimmed for a laptop cluster (`deploy/monitoring/values.yaml`: emptyDir storage, 6h retention, small resource requests). Called by the Tiltfile's `monitoring` resource locally and by `bootstrap-prod.sh` step 5 for prod — same script, same chart version, both paths. `--wait --timeout 10m` (widened from 5m after a real cold-start run hit the old timeout under CPU-throttled conditions — see `WORKLOG.md`). `MONITORING=false bash scripts/bootstrap-prod.sh` skips it on the prod path if you want a faster iteration loop there.

The `monitoring` namespace carries `pod-security.kubernetes.io/enforce: privileged` (`deploy/k8s/namespace-monitoring.yaml`) — a deliberate, scoped exception to the `restricted` posture `pokeproxy`'s namespace enforces, needed because `prometheus-node-exporter` runs with `hostNetwork`/`hostPID`/hostPath mounts on `/proc`,`/sys` by design, which both `baseline` and `restricted` forbid.

The app chart renders a `ServiceMonitor`, a `PrometheusRule` (3 alerts), and a Grafana dashboard ConfigMap under `.Values.monitoring.enabled` (`true` in both `deploy/envs/local/values.yaml` and `deploy/envs/prod/values.yaml`). A `NetworkPolicy` scoped to `namespaceSelector: monitoring` **and** `podSelector: app.kubernetes.io/name=prometheus` — not the whole namespace — is what actually lets the scrape through.

**Grafana** is on the Ingress, path-based, no port-forward needed: `http://localhost:8080/grafana/` locally (`:8081` for prod) — the "PokeProxy Overview" dashboard is also the Grafana *home* dashboard. Login `admin` / `pokeproxy-dev` — a fixed dev-only password (`deploy/monitoring/values.yaml`'s `grafana.adminPassword`), not a security boundary; see A-2 in `docs/planning/part-05-automation.md` for why it's pinned instead of auto-generated. A plain `kubectl port-forward ... 3000:80` + `localhost:3000/` doesn't work on its own: `grafana.ini`'s `serve_from_sub_path`/`root_url` (needed so the Ingress path works at all) makes Grafana 301 `localhost:3000/` to `http://localhost/grafana/` — the port gets dropped in the redirect target. If you do port-forward, go straight to the sub-path: `localhost:3000/grafana/`.

```bash
kubectl --context k3d-pokeproxy port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090
```

**Prometheus is deliberately not on the Ingress** (A-3, `docs/planning/part-04-observability.md` § Requirement audit): its read API has no auth of its own in this chart, and an Ingress would put all of it on the open internet unauthenticated in both dev and prod. Port-forward-only keeps it consistent with the app's own `/metrics` and `/stats` staying off the Ingress too. `localhost:9090/targets` shows the `pokeproxy` scrape target; `/alerts` shows the three `PokeProxyHighServerErrorRate`/`PokeProxyCacheBackendErrors`/`PokeProxyTargetsDown` rules. Full design rationale, panel list, and alert threshold justifications: `docs/planning/part-04-observability.md`.

## Environments

Per-environment values live outside the chart, at `deploy/envs/<env>/values.yaml`. Helm has never required a values file to sit inside the chart directory, and keeping them out means Argo CD and the local Tiltfile can point at the same files without the chart carrying environment-specific state.

| Env | Values | Sealing key | Cluster / context | Port |
|---|---|---|---|---|
| `local` | `deploy/envs/local/values.yaml` | `.secrets/sealing-key-local.yaml` (auto-minted, D5) | `pokeproxy` / `k3d-pokeproxy` | 8080 |
| `prod` | `deploy/envs/prod/values.yaml` | `.secrets/sealing-key-prod.yaml` (manual, D6 — see below) | `pokeproxy-prod` / `k3d-pokeproxy-prod` | 8081 |

## GitOps (prod stand-in)

There is no real production cluster for this assignment, so `prod` is a second k3d cluster on the same laptop: its own context, its own Argo CD, its own sealing key, its own port. That converts the CD half of Part 3 from described to demonstrated. Prod deliberately stays script-driven rather than moving to Tilt (D9) — Tilt is the dev inner loop, Argo CD is the delivery mechanism, and conflating them would blur exactly the distinction Part 3 exists to demonstrate.

```bash
make up-prod    # or: bash scripts/bootstrap-prod.sh
```

Re-run this session end to end: `make_up_prod_exit=0`, `sync=Synced health=Healthy`, ingress probe `http_code=401`. Idempotent, and the order matters:

| # | Step | Why here |
|---|---|---|
| 0 | Preflight | same checks as local — fails before anything is created |
| 1 | Create `pokeproxy-prod` if absent | reuses an existing cluster rather than failing on it |
| 2 | Apply `deploy/k8s/namespace.yaml` | PSA labels must exist before any workload lands |
| 3 | `seal-hmac.sh --env prod` | pins the sealing key from `scripts/init-sealing-key.sh --env prod` (run once, separately — see below) **before** installing the controller, so the controller adopts our key instead of minting its own. **Exits 1 immediately if that key was never provisioned or restored**, instead of silently minting a fresh one that can't decrypt the committed ciphertext (F-2) |
| 4 | Install Argo CD | needs the SealedSecret CRD from step 3 to be able to sync the chart |
| 5 | Monitoring stack (`scripts/install-monitoring.sh`) | must exist **before** step 6, so the app chart's `ServiceMonitor`/`PrometheusRule` have a running Prometheus Operator and cross-namespace CRD watch to be discovered by. `MONITORING=false` skips it |
| 6 | Apply the `Application` | renders the app chart's `monitoring.enabled: true` (prod, set in `deploy/envs/prod/values.yaml`) objects against the stack step 5 just installed |
| 7 | Wait for `Synced` / `Healthy`, then probe the ingress | fails loudly with the Application and pod state if it doesn't converge |

The namespace is deliberately **not** owned by Argo CD (`CreateNamespace=false`): its PSA labels are a cluster-admin concern, and letting the app's own sync manage the boundary it runs inside is a circularity I'd rather not have.

### Sealing key (prod, manual — unchanged from Part 3)

```bash
bash scripts/init-sealing-key.sh --env prod
```

*(Not re-run this session — it already exists from earlier work and the script itself refuses to run twice against an existing key; see below.)* First time only, per environment. Refuses to run a second time against an existing key file — read its output for the disaster-recovery procedure if you actually need to rotate one. **Back the printed file up somewhere durable before doing anything else**; it's gitignored, and losing it makes every ciphertext ever sealed against it permanently undecryptable. Deliberately kept manual, unlike local: Argo CD reads **git**, not the working tree, so automating this would mean committing and pushing a re-sealed `deploy/envs/prod/values.yaml` on every bootstrap (evaluated and rejected — `docs/issues/023`).

### Teardown

```bash
make down-prod    # or: bash scripts/down-prod.sh
```

Re-run this session: `make_down_prod_exit=0`, cluster deleted, `.secrets/sealing-key-prod.yaml` left untouched (it's the only backup of that key — the script never touches it).

### What Argo CD watches

`deploy/argocd/application.yaml` — `repoURL` the GitHub repo, `targetRevision: main`, `path deploy/helm/pokeproxy`, `helm.valueFiles: ["../../envs/prod/values.yaml"]`. Helm `valueFiles` may resolve outside the Application's `path`; the boundary Argo CD enforces is the **repository root**, not the app path.

`syncPolicy.automated` with `prune` and `selfHeal`, plus `retry.limit: 3` with exponential backoff. The bound is load-bearing: `selfHeal` combined with a PostSync E2E that keeps failing would otherwise re-sync and re-run the check against a broken deployment indefinitely.

Argo CD reads from **GitHub, not the working tree** — an uncommitted or unpushed change is invisible to it. For verifying a branch before it merges, `ARGOCD_TARGET_REVISION=<branch> bash scripts/bootstrap-prod.sh` points the Application at that branch instead of `main`.

### Reconciliation interval

`deploy/argocd/install-values.yaml` sets `timeout.reconciliation: 30s`, down from the chart default of 180s. Stated plainly because it directly moves the "commit to serving" number: a real setup would make this event-driven with a repo webhook (near-zero delay) rather than shortening a poll. Polling at 30s is the honest local stand-in for that, not a claim about how fast Argo CD is by default.

### Image references

`deploy/envs/prod/values.yaml` pins **tag and digest** per image; the chart's `pokeproxy.image` helper emits `repository@digest` when a digest is set and falls back to `repository:tag` when it isn't. A git sha makes a tag *unique*, not *immutable* — a rebuild against a moved base image republishes different bytes under the same tag, and `IfNotPresent` then leaves different nodes running different builds. The triples are seeded by hand once and owned by CI's promote job from then on.

### Delta from a real production

| Here | A real production |
|---|---|
| `mock-downstream.enabled: true` — the E2E's delivery assertion needs a sink inside the cluster | a real downstream, with the E2E asserting against a synthetic endpoint instead, and `allow-pokeproxy-egress-to-dependencies` extended to reach it |
| The E2E Job mounts the real HMAC signing key | a dedicated test credential — not possible today, the app validates against a single key (M3) |
| Sealing key generated locally and gitignored | a KMS-backed or externally managed key, with the public half committed |
| `server.insecure: true`, port-forward access | TLS termination and real SSO in front of the Argo CD server |

## CI promote (main only)

The `promote` job in `.github/workflows/ci.yml` runs after `build-pokeproxy`, `build-mock-downstream` and `build-e2e` succeed on a push to `main` — never on a pull request, so opening a PR never writes to the repo. It writes the six tag/digest fields into `deploy/envs/prod/values.yaml` with `yq` (preinstalled on `ubuntu-latest`), commits as `github-actions[bot]` with subject `chore(deploy): promote <sha>` and the three digests in the body, then `git pull --rebase` and pushes to `main` directly — no PR, since a promote-opens-a-PR design would need a human merge on every deploy, trading lead time for a review step nothing in this pipeline benefits from at this size.

**Branch protection: what actually blocked this, live.** `main`'s classic protection has `required_pull_request_reviews` present (0 required approvals, but the object's mere presence means "a PR is required" on its own) — a direct push authenticated with the default `GITHUB_TOKEN` was rejected: `GH006: Changes must be made through a pull request`, unaffected by `enforce_admins: false` because that only exempts a *human* pushing with their own admin credentials, not an App-token push. Two things tried and ruled out, worth recording since both looked plausible first:

- **A repo Ruleset with an `Integration`-type bypass actor for the `github-actions` App** (id `15368`, confirmed via `gh api apps/github-actions`) — rejected by GitHub's own validation: `"Actor GitHub Actions integration must be part of the ruleset source or owner organization"`. This repo is user-owned, not org-owned, so there is no org context for the app to belong to — a hard platform limitation for personal repos, not a config mistake.
- **promote opens and self-merges a PR** (viable, since 0 approvals are required) — rejected on a stated design preference: the promote commit should land on `main` directly, not via a merge commit produced by an API-merged PR.

**What actually works: a fine-grained PAT.** A personal access token scoped to only this repo, `Contents: Read and write`, stored as the `PROMOTE_PUSH_TOKEN` secret. `actions/checkout`'s `token:` input swaps it in for the job's git credentials, so the later `git push` authenticates as an actual repo admin — and `enforce_admins: false` exempts that from the PR requirement, the same mechanism `gh pr merge --admin` uses. Smaller blast radius than a classic PAT (`repo` scope reaches every repo on the account); the honest gap is that it's a long-lived credential with no rotation built in, which a real production setup would replace with a GitHub App installation token instead.

**Idempotent by design, not by accident.** If the digests already match (nothing changed since the last promote — e.g. a docs-only commit still runs the full build), the job diffs clean and exits without committing. `yq -i` re-serializes the whole file, which drops the blank lines between blocks on every real promote; no comments or keys are lost, only cosmetic spacing.

**Why this doesn't loop.** Pushing with a real PAT is not the same as pushing with the default `GITHUB_TOKEN` — GitHub only suppresses workflow retriggering for the latter, so a PAT-authenticated push to `main` would otherwise kick off a second CI run, which would build, promote again, and repeat. The commit subject carries `[skip ci]`, which GitHub's event dispatcher honors on any push regardless of the authenticating credential — the one loop-prevention mechanism that doesn't depend on which token did the pushing. Verified against a real run rather than assumed; see `WORKLOG.md`.

**Commit-to-serving.** Measured, not claimed: from the merge commit landing on `main` to the prod pod's running image digest matching what promote wrote. Recorded in `WORKLOG.md` for the specific run that produced the number, since it depends on Argo's `timeout.reconciliation` (30s here) and is not a general guarantee.

**Image signatures are not verified anywhere in this chain (F-10).** `cosign sign --yes` runs after every build, but nothing on the pull side — no admission policy in either cluster, no `cosign verify` step before Argo syncs — checks a signature before running the image. `cosign verify` was run once by hand against the real GHCR artifacts as a manual proof the chain works end to end (see `WORKLOG.md`), not as a standing control. Treat signing today as provenance for a human to check after the fact, not as a gate. A real deployment would add a Kubernetes admission policy (e.g. `cosign`'s own policy-controller, or Kyverno's `verifyImages`) requiring a valid signature from the expected OIDC identity before a pod is admitted.

## Rollback

`rollback.yml` (`workflow_dispatch`, input `sha`) resolves that sha's three image digests from GHCR via `docker buildx imagetools inspect`, writes them into `deploy/envs/prod/values.yaml` with the same `yq` pattern as `promote`, lints and validates the rendered chart before committing, then pushes `revert(deploy): roll back to <sha> [skip ci]` to `main` through the same `PROMOTE_PUSH_TOKEN` path. Argo reconciles it like any other commit.

```bash
gh workflow run rollback.yml -f sha=<7-char short sha>
```

*(Not re-run this session — unchanged Part 3 content with its own real evidence below, from run [32666881696](https://github.com/IdanBro/PokeProxy/actions/runs/32666881696).)*

**Scope: images only, by design (F-7).** This reverts the three `components.*.image.{tag,digest}` and `e2e.image.{tag,digest}` fields — nothing else in `deploy/envs/prod/values.yaml` or the chart. If a bad deploy came from a chart/manifest/values change instead of a bad image (probes, NetworkPolicy, resource limits, rules), the correct response is a plain `git revert <merge-commit>` on `main`, pushed through the normal PR path — `rollback.yml` does not cover that case and is not meant to. Two different failure classes, two different tools; this section previously implied a single "git revert" mechanism for both, which was imprecise.

**Executed live against the real prod cluster, 2026-08-23** (`WORKLOG.md`, "Step 6 scenarios A and B" / "Scenario C — executed live"). All three scenarios from `docs/planning/part-03-cicd-gitops.md` ran with captured evidence:

| Scenario | Setup | Result |
|---|---|---|
| A — rollout failure | `kubectl set image` to a non-existent tag | new pod stuck `ImagePullBackOff`; both old pods stayed `Running` throughout (`maxUnavailable: 0`); load generator measured 241 requests, 0 errors |
| B — verification failure | rules ConfigMap edited to a wrong `reason`, pods healthy but functionally wrong | PostSync E2E pod: `phase=Failed exitCode=1`, exact assertion mismatch logged; recovery confirmed via `selfHeal` restoring the correct ConfigMap and a fresh ReplicaSet |
| C — bad version found later | `gh workflow run rollback.yml -f sha=b281080` against a healthy prod | run [32666881696](https://github.com/IdanBro/PokeProxy/actions/runs/32666881696) succeeded; commit landed on `main` with no PR; all six digests matched; `[skip ci]` suppressed a second CI run; `bootstrap-prod.sh` confirmed prod reconciled with a passing PostSync E2E and a correct `401` on a live probe |

Part 3 step 6 and DoD item 9 are closed on this evidence. Locally, the same class of failure (B) is reproducible on demand — see the "Send signed traffic" / "Break rules.json (scenario B)" / "Restore rules.json" buttons in the root `README.md`.
