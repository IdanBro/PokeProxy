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

Every step below, and `scripts/deploy.sh`, target the `k3d-pokeproxy` context explicitly rather than whatever context happens to be current — set `KUBE_CONTEXT=<name>` to override. The scripts never change your current context.

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
kubectl --context k3d-pokeproxy apply -f deploy/k8s/namespace.yaml
```

This carries the `pod-security.kubernetes.io/{enforce,audit,warn}: restricted` labels that make the security posture in the chart's Deployments an enforced invariant, not just a claim. Idempotent — safe to re-run.

## 4. Seal the HMAC secret

```bash
bash scripts/seal-hmac.sh --env local
```

Generates (or reuses) a Sealed Secrets sealing key at the gitignored `.secrets/sealing-key-local.yaml`, installs the Sealed Secrets controller, and writes sealed ciphertext into `deploy/envs/local/values.yaml`. `--env prod` is the same procedure against the prod stand-in cluster (`.secrets/sealing-key-prod.yaml`, `deploy/envs/prod/values.yaml`); it defaults to `local`. Safe to re-run: it only re-seals when a new sealing key was actually generated — i.e. a fresh clone, which has no `.secrets/`. A fresh *cluster* with the key still on disk correctly reuses the existing ciphertext.

## 5. Deploy

```bash
SHA=$(git rev-parse --short HEAD)
helm upgrade --install pokeproxy deploy/helm/pokeproxy \
  --kube-context k3d-pokeproxy \
  -n pokeproxy \
  -f deploy/envs/local/values.yaml \
  --set components.pokeproxy.image.tag=$SHA \
  --set components.mock-downstream.image.tag=$SHA \
  --atomic --timeout 3m
```

`--atomic` rolls back automatically if the release doesn't reach a healthy state within the timeout.

## 6. Verify

```bash
kubectl --context k3d-pokeproxy get pods -n pokeproxy
curl -i -X POST http://localhost:8080/stream   # expect 401 — no signature on this plain request
```

`/stream` is a POST-only route — a bare `curl -i http://localhost:8080/stream` sends a GET and returns **405**, not 401. Use `-X POST` for the intended check.

For a real signed request, `app/scripts/load_generator.py` builds valid protobuf + HMAC payloads:

```bash
cd app && python scripts/load_generator.py --url http://localhost:8080/stream --rps 1 --duration 5
```

## Teardown

```bash
k3d cluster delete pokeproxy
```

Deletes the cluster and everything in it. `.secrets/sealing-key-local.yaml` and `deploy/envs/local/values.yaml` are left on disk — re-running steps 1–5 reuses them if the key is still valid, or regenerates and re-seals automatically if not (step 4's idempotency).

## Environments

Per-environment values live outside the chart, at `deploy/envs/<env>/values.yaml`. Helm has never required a values file to sit inside the chart directory, and keeping them out means Argo CD and `deploy.sh` can point at the same files without the chart carrying environment-specific state.

| Env | Values | Sealing key | Cluster / context |
|---|---|---|---|
| `local` | `deploy/envs/local/values.yaml` | `.secrets/sealing-key-local.yaml` | `pokeproxy` / `k3d-pokeproxy`, port 8080 |
| `prod` | `deploy/envs/prod/values.yaml` | `.secrets/sealing-key-prod.yaml` | `pokeproxy-prod` / `k3d-pokeproxy-prod`, port 8081 |

The old chart-internal `values-prod.yaml` was deleted rather than moved: it described an environment that did not exist and could not deploy (see S4 in `WORKLOG.md`). The prod environment and its Argo CD bootstrap are Part 3 step 4b.

## GitOps (prod stand-in)

There is no real production cluster for this assignment, so `prod` is a second k3d cluster on the same laptop: its own context, its own Argo CD, its own sealing key, its own port. That converts the CD half of Part 3 from described to demonstrated.

```bash
bash scripts/bootstrap-prod.sh
```

Idempotent, and the order matters:

| # | Step | Why here |
|---|---|---|
| 1 | Create `pokeproxy-prod` if absent | reuses an existing cluster rather than failing on it |
| 2 | Apply `deploy/k8s/namespace.yaml` | PSA labels must exist before any workload lands |
| 3 | `seal-hmac.sh --env prod` | generates `.secrets/sealing-key-prod.yaml` and applies it **before** installing the controller, so the controller adopts our key instead of minting its own |
| 4 | Install Argo CD | needs the SealedSecret CRD from step 3 to be able to sync the chart |
| 5 | Apply the `Application` | |
| 6 | Wait for `Synced` / `Healthy`, then probe the ingress | fails loudly with the Application and pod state if it doesn't converge |

The namespace is deliberately **not** owned by Argo CD (`CreateNamespace=false`): its PSA labels are a cluster-admin concern, and letting the app's own sync manage the boundary it runs inside is a circularity I'd rather not have.

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
