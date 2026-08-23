# Part 3 — CI/CD & GitOps

## What I am actually solving

Part 2 made the service deployable. Every deployment so far has been a human running `scripts/deploy.sh` on their own laptop against a cluster they created by hand. Nothing gates `main`, no artifact is published anywhere, and the only proof a deploy worked is that someone ran `curl` afterwards and looked at the output.

Part 3 turns that into a pipeline: a commit is linted and tested, becomes a scanned, signed, immutable published image, becomes a change to desired state in git, and is reconciled into a cluster by an agent — then verified by real signed protobuf traffic before anyone calls it done.

Two Part 2 items close here:

| ID | Gap | Closes in |
|---|---|---|
| S4 | `values-prod.yaml` is undeployable on two counts — rules point at the mock Service it disables, and the egress NetworkPolicy blocks any external downstream | step 4 (deleted, superseded by `deploy/envs/prod/values.yaml`) |
| N7 | `scripts/seal-hmac.sh:96` rewrites its target wholesale with `cat >`, discarding anything else in the file | step 4 (mandatory — the target will hold image tags and digests) |

And one failure class disappears structurally:

**Sha-drift**, recorded three separate times in Part 2 (steps 6, 7, 8). A session gap between "build the image" and "deploy it" leaves the cluster running an image tagged for an older sha, and `k3d image import` succeeds either way, so it only surfaces as `ImagePullBackOff` on the next deploy. Once the cluster can only run a digest CI published, this becomes impossible rather than merely unlikely.

## The constraint that determined the architecture

**GitHub-hosted runners cannot reach a Kubernetes cluster running on my laptop.** There is no inbound route and there will not be one.

Every design where CI runs `kubectl` or `helm` against the target cluster therefore requires a self-hosted runner on this machine — which is exactly the anti-pattern of CI mutating cluster state behind git's back. A pull-based agent inside the cluster is not a stylistic preference here; it is the only thing that connects cloud CI to this cluster at all.

That single fact picks Argo CD, and everything else follows from it.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Argo CD**, pull-based reconciliation | see above; also what the assignment suggests |
| D2 | **A second k3d cluster** `pokeproxy-prod` (port 8081, own context, own Argo CD, own sealing key) stands in for production | I have no production cluster. A second local cluster is genuinely a *different* cluster — separate control plane, separate context, images pulled over the network from a real registry. The only unfaithful thing is the address of the machine |
| D3 | **Short-sha tags** (`:a1b2c3d`) **plus digest pinning** (`repo:tag@sha256:...`) | the tag is for the human reading the diff; the digest is what Kubernetes pulls |
| D4 | Rollback is `rollback.yml`, **human-triggered**. Argo CD Notifications auto-revert is **documented, not built** | a flaky E2E would otherwise become an automatic production change |
| D5 | The E2E is a **four-line image derived `FROM` the app image** | keeps test code out of production, makes protobuf-version drift impossible, stays portable for the ephemeral-CI runner we may add later |
| D6 | E2E traffic goes **through Traefik**, not straight to the Service | exercises the real edge path, including the 1 MiB body middleware from Part 2 step 8 |
| D7 | **CI never touches any cluster** | promotion is a git commit and nothing else. That is the whole GitOps claim, and it either holds literally or it is not GitOps |
| D8 | `ruff format --check` is **not** added | scope. It would reformat 13 of 27 files, and a formatting-only diff is noise in a submission a human reads. `ruff check` already gates correctness. See the note below — my first justification for this was weak |
| D9 | **Env values live in `deploy/envs/<env>/values.yaml`**, outside the chart; `values-prod.yaml` is deleted | the chart is a reusable artifact, environment values are config. Mixing them means the chart can never be versioned or published independently |
| D10 | **Supply chain: scan, attest, sign.** Trivy gating HIGH/CRITICAL, SBOM + SLSA provenance from buildx, cosign keyless signing | publishing unscanned, unattested images is not defensible in 2026, and all three are cheap |
| D11 | **Branch protection on `main` requiring the CI checks**, plus a GitHub Environment on the promote job | CI that cannot block a merge is not a gate. The Environment gives a deployment audit trail and a place to add a required reviewer |

### On D3 — why a commit sha is not enough on its own

The commit sha is immutable. The *tag* is not: `ghcr.io/idanbro/pokeproxy:a1b2c3d` is a rewritable label pointing at a manifest digest. Naming it after a commit buys uniqueness and traceability by convention, not enforcement.

The realistic way it breaks: re-run the build job for a commit (flaky runner, expired token), the base image `python:3.13-slim-bookworm` has moved underneath us since — that is N5 — so the rebuild produces different bytes under the same tag. With `imagePullPolicy: IfNotPresent`, a node that already pulled keeps the old layers and a node that scales up later gets the new ones. Two builds serving under one version string, with `kubectl describe` showing the same tag on both.

The digest is the hash of the manifest itself, so it cannot point at different bytes. Cost of pinning it is roughly two lines of Helm and reading a value CI already produces.

**Consequence of D10 on D3:** enabling SBOM and provenance makes buildx publish an OCI image *index* even for a single platform, so the digest we pin is the index digest. containerd pulls that correctly, but it is exactly the kind of thing that is fine until it isn't — step 4 verifies the prod cluster actually pulls a provenance-carrying index digest rather than assuming it.

### On D8 — correcting my own reasoning

I originally justified skipping `ruff format` with "a formatting sweep would bury real history." That is not a good reason: `.git-blame-ignore-revs` is the standard remedy for exactly this, and I should have said so. The honest reason is scope — a 13-file formatting diff adds noise to a submission that a human reads, and correctness is already gated by `ruff check`. If this repo were long-lived and multi-contributor, I would do the sweep and add the ignore-revs file.

### On D9 — reversed mid-planning, and why

An earlier draft of this plan kept env values inside the chart directory, justified by a claim that Argo CD rejects Helm `valueFiles` resolving outside the Application's `path`. **That claim was wrong**, and it is worth recording rather than quietly fixing.

The actual restriction is the **repository root**, not the app path. Relative paths such as `../../envs/prod/values.yaml` are supported in a single-source Application; only escaping the repo root — including via a symlink — is rejected. Multi-source with `$values` exists for values in a *different repository*, which is not our case.

So the separated layout stands, on its own merits rather than by default. `helm -f` has never required a values file to live inside the chart, so the local path is unaffected:

```bash
helm upgrade --install pokeproxy deploy/helm/pokeproxy -f deploy/envs/local/values.yaml
```

Only `values.yaml` — the defaults — stays in the chart.

### What was actually wrong with `values-prod.yaml` (S4)

It lints clean, renders clean, and would not work:

1. It sets `mock-downstream.enabled: false` but the routing rules still resolve to the mock's Service address. The app would start and then fail every forward, pointing at a Service that does not exist. Nobody filled in real URLs because there is no real downstream.
2. `allow-pokeproxy-egress-to-dependencies` permits egress only to Redis and the mock. A genuinely external downstream would be blocked at the pod even with correct URLs.

`deploy/envs/prod/values.yaml` supersedes it with a file describing an environment that actually exists. The external-production delta — real URLs, a widened egress rule, nginx ingress — goes into `deploy/README.md` as prose, because prose cannot be deployed and broken.

## Environment model

| Env | Cluster / context | Ingress | Deployed by | Images | Real? |
|---|---|---|---|---|---|
| **dev** | `k3d-pokeproxy` (exists) | `localhost:8080` | `scripts/deploy.sh` then Helm `--atomic` | built locally, `k3d image import` | yes, today |
| **prod** | `k3d-pokeproxy-prod` (new) | `localhost:8081` | **Argo CD**, reconciling `main` | `ghcr.io/idanbro/*`, pinned by digest | yes, after step 4 |
| external prod | none | — | — | — | prose in `deploy/README.md` only |

No ownership conflict between Helm and Argo CD: they own different clusters.

## Technologies chosen

| Area | Choice | Why |
|---|---|---|
| CI | **GitHub Actions** | repo already lives there; `GITHUB_TOKEN` authenticates to GHCR with no managed secret |
| Registry | **GHCR** `ghcr.io/idanbro/*` | repo is public, so the packages can be public and the cluster pulls anonymously — no imagePullSecret. Docker Hub would mean a managed credential plus anonymous pull-rate limits on every pod restart |
| CD | **Argo CD**, one Application, Helm source | pull-based, egress-only, has a UI that makes reconciliation demonstrable |
| Desired state | `deploy/envs/prod/values.yaml`, same repo | one clonable artifact; `GITHUB_TOKEN` pushes do not trigger workflows, so recursion is structurally prevented |
| Image scanning | **Trivy**, `severity HIGH,CRITICAL`, `ignore-unfixed`, `exit-code 1` | `ignore-unfixed` because failing on a vulnerability with no available fix trains people to ignore the gate. Documented exceptions go in `.trivyignore` |
| Attestation | buildx `sbom: true`, `provenance: mode=max` | two lines; SLSA provenance and an SBOM attached to the image |
| Signing | **cosign**, keyless via GitHub OIDC | no key material to manage; pairs naturally with digest pinning |
| Dependency updates | **Dependabot** — `github-actions`, `docker` | digest-pinned base images are unmaintainable without a bot. Trading drift for staleness is not a fix |
| Verification | one Python script, one derived image, run as a hook Job | identical logic under Helm locally and Argo CD in prod |
| Value editing in CI | **yq**, pinned | `sed` on YAML is how you lose a sealed ciphertext |

## Alternatives I considered

**Flux instead of Argo CD.** Genuinely the stronger rollback story: `HelmRelease.spec.test` plus `upgrade.remediation` gives *automatic* rollback when the post-deploy test fails, which Argo CD does not do natively. Its image-automation controller would also update the tag in git by itself, so CI would not need write access to the repo at all.

Chose Argo CD anyway, for three reasons. The assignment names it. Its UI makes "who reconciles what" demonstrable in a way `flux get` does not. And the substantive one: automatic remediation matters most when post-deploy verification is the *only* gate — the moment an ephemeral pre-promotion cluster exists (see "What is deliberately deferred"), verification moves upstream of the cluster and auto-remediation becomes a nice-to-have. If someone pushes back on this, Flux is the answer I would switch to, and nothing in the chart depends on Argo.

**A separate config repository.** The correct shape at scale: independent RBAC, deploy history not interleaved with app history, no recursion question at all. Rejected because the submission has to be one clonable artifact, and the recursion risk that normally justifies the split is already handled — commits pushed with the default `GITHUB_TOKEN` do not trigger `push` workflows.

**A self-hosted runner on this laptop.** Would let CI deploy directly and would be far less machinery. Rejected outright: cluster state would become a side effect of a job, git would never describe what is running, and calling it GitOps would be false.

**An ephemeral k3d cluster inside the CI runner as a pre-promotion gate.** My original recommendation, and still the strongest available gate — it blocks a bad image from ever entering desired state. Deferred deliberately (see below). Its absence is the main honest weakness of this design and is quantified under "Verification and rollback".

**External Secrets Operator instead of Sealed Secrets.** ESO is now the more common choice in real production: secrets live in a managed store (Vault, AWS/GCP secret managers) and never enter git at all, encrypted or not, which also removes the sealing-key-portability problem entirely. Rejected here only because it requires an external secret store that does not exist for this assignment. If this were a real cluster with a cloud provider behind it, ESO is what I would use, and Sealed Secrets would be the fallback for air-gapped or provider-less environments.

**Digest-only pinning, no tag.** Strictly the purest form, and rejected on operability: `git log -p` on desired state becomes forty characters of hex with nothing to tell a human which commit shipped. Carrying both costs one extra field.

**Argo CD creating the namespace** via `CreateNamespace=true` plus `managedNamespaceMetadata`. Rejected — `deploy/k8s/namespace.yaml` already carries the PSA labels and was added specifically because the namespace was untracked (B2). Two owners for one object is how those labels silently drift.

**Argo CD managing itself (app-of-apps).** The standard answer to "who watches the watcher" — Argo CD's own install becomes an Application it reconciles. Not doing it here: `bootstrap-prod.sh` has to install Argo CD imperatively anyway for the very first apply, and self-management mainly buys drift correction on the Argo install itself, which is not a risk on a cluster that is recreated freely. Documented because it is the expected follow-up question.

**The promote job opening a pull request instead of committing directly.** Higher ceremony, and the right call when the config change needs review. Rejected for an automated image bump that has already passed lint, test and scan — the review would be a rubber stamp on two changed lines. The GitHub Environment (D11) provides the audit trail that a PR would otherwise provide, and adding a required reviewer there is a one-setting change if that ever becomes wanted.

## Final pipeline

```
 push to main
      |
      v   GitHub Actions - never touches a cluster
 +---------------------------------------------------------------+
 | lint      ruff check                                           |
 | test      pytest (106)                                         |
 | chart-lint  helm lint --strict | kubeconform   (UNGATED - F-4)  |
 |   +--> build    buildx x3 -> GHCR :<short-sha>
 |         |        sbom: true, provenance: mode=max
 |         +--> scan     trivy, HIGH/CRITICAL, ignore-unfixed
 |               +--> sign    cosign keyless (OIDC)
 |                     +--> promote  [environment: production]
 |                            yq-write tag+digest into
 |                            deploy/envs/prod/values.yaml,
 |                            commit as github-actions[bot], push
 +---------------------------------------------------------------+
      |   ([skip ci] on the promote commit => no workflow recursion.
      |    GITHUB_TOKEN's own suppression does NOT apply: the job
      |    pushes with a PAT. Corrected from the design assumption.)
      v
 Argo CD in k3d-pokeproxy-prod, polling main every 30s
      |-- sync: helm template + apply       (retry limit 3, backoff)
      |-- rollout: maxUnavailable 0 + probes
      +-- PostSync Job: real protobuf + HMAC through Traefik,
                        assert the payload reached mock /received
             pass -> Synced / Healthy
             fail -> Sync Failed, Degraded
                       --> gh workflow run rollback.yml -f sha=<last-good>
                             --> revert commit --> Argo reconciles back
```

Publishing happens before scanning deliberately: the sha tag is immutable and nothing references it until the promote commit, so an unscanned image in the registry is inert. **Promotion is the gate, not publication** — the "build once, promote by reference" model. Scanning by digest after push also avoids `load: true`, which would defeat the multi-stage build cache.

| Role | Who |
|---|---|
| Build, scan, sign and publish the image | GitHub Actions |
| Update desired state | GitHub Actions, as a commit |
| Reconcile desired state | Argo CD, inside the prod cluster |
| Verify the running result | Argo CD PostSync Job, inside the prod cluster |
| Roll back | `rollback.yml`, human-triggered |

## Repository layout

```
.github/
  workflows/
    ci.yml                          NEW   lint / test / build / scan / sign / promote
    rollback.yml                    NEW   workflow_dispatch
  dependabot.yml                    NEW   github-actions + docker ecosystems
app/
  e2e/e2e_check.py                  NEW   the one verification script
  Dockerfile.e2e                    NEW   FROM the app image + COPY
  .trivyignore                      NEW   documented scan exceptions (empty to start)
deploy/
  helm/pokeproxy/
    values.yaml                     MOD   defaults only; + e2e block, + image.digest
    values-local.yaml               DELETED -> deploy/envs/local/values.yaml
    values-prod.yaml                DELETED -> deploy/envs/prod/values.yaml (S4)
    templates/_helpers.tpl          MOD   image-reference helper -> repo:tag@digest
    templates/e2e/job.yaml          NEW   dual-annotated hook Job
    templates/networkpolicy.yaml    MOD   +3 e2e rules
  envs/
    local/values.yaml               NEW   dev desired state (moved)
    prod/values.yaml                NEW   GitOps desired state; the only file CI writes
  argocd/
    install-values.yaml             NEW   pinned Argo CD config, 30s reconciliation
    application.yaml                NEW   the Application CR
  k3d/cluster.yaml                        dev, 8080 (unchanged)
  k3d/cluster-prod.yaml             NEW   prod stand-in, 8081
  k8s/namespace.yaml                      unchanged
  README.md                         MOD   GitOps section + external-prod delta
scripts/
  deploy.sh                         MOD   values path, e2e image, --short=7, full-sha label
  seal-hmac.sh                      MOD   --env {local,prod}; N7 fix
  bootstrap-prod.sh                 NEW   cluster / key / sealed-secrets / Argo CD / Application
docs/
  planning/part-03-cicd-gitops.md   this file
  issues/021...                     NEW   one per real issue fixed
```

## Facts this plan rests on, verified before writing it

| Check | Result | Consequence |
|---|---|---|
| Traefik Service | `traefik.kube-system`, `80:32637/TCP`, ClusterIP `10.43.137.229` | the E2E Job's in-cluster URL is `http://traefik.kube-system.svc.cluster.local:80/stream` |
| Traefik's Service port 80 vs its container port | Service `web` port 80 maps to a **named** targetPort `web` = container port **8000** — found by debugging a NetworkPolicy that refused connections despite a syntactically correct rule | this cluster's NetworkPolicy enforcement matches the destination pod's real listening port, not the Service port a client dials; `egress.ports` in a cross-namespace rule must reference 8000, not 80. Exposed as `e2e.traefikContainerPort` in values.yaml so it doesn't drift silently if Traefik's chart changes it |
| This cluster's NetworkPolicy deny behavior | A denied destination gives immediate `ECONNREFUSED`, not a timeout — confirmed by isolating a debug pod outside all policies (worked) vs. inside (refused instantly) | corrects step 3's verification table, which originally assumed a timeout |
| `main` branch protection | none at design time — being enabled (D11) | the promote job will need a documented bot bypass |
| Docker server arch | `amd64` | build `linux/amd64` only |
| `app/.python-version` | `3.13` | uv resolves it from the file; no version input in the workflow |
| `git rev-parse --short HEAD` | **7 characters** | matches `${GITHUB_SHA:0:7}`; CI and `deploy.sh` will agree |
| Is the downstream forward synchronous? | **yes** — `proxy.py:123` awaits `_forward_with_retry` before responding | when the E2E receives its 200, the mock has already recorded the payload. No polling race |
| `ruff format --check` | would reformat **13 of 27** files | D8 |
| Repo visibility | PUBLIC | GHCR packages can be public; anonymous pull |
| Local `gh` token scopes | `repo, read:org, gist, admin:public_key` — no `write:packages` | irrelevant: Actions publishes, the cluster only pulls |
| Argo CD `valueFiles` scope | bounded by the **repository root**, not the Application `path`; `../../envs/prod/values.yaml` is valid | D9 — the separated layout works with a single-source Application |
| Argo CD Helm-hook handling | maps `post-install`/`post-upgrade` to `PostSync`; **if any Argo hook annotation is present, all Helm hooks are ignored**; `helm.sh/hook: test` has no equivalent and is skipped | dual annotation gives exactly one execution per path. Using `helm.sh/hook: test` would have silently never run in prod |

## Order of work

| # | Step | Size | State |
|---|---|---|---|
| 1 | CI lint + test (`.github/workflows/ci.yml`), Dependabot, planning doc, WORKLOG | S | **Done** — verified live on PR #3, see WORKLOG |
| 2 | CI build + push to GHCR with SBOM/provenance, Trivy scan, cosign signing | M | **Done** — verified live on PR #3, see WORKLOG |
| 3 | **E2E: script, derived image, hook Job, 3 NetworkPolicy rules** — proven on the existing dev cluster | **L** | **Done** — verified live, see WORKLOG |
| 4 | Prod stand-in cluster + Argo CD + `deploy/envs/` move (closes S4, N7) | M–L | **Done** — split 4a/4b, verified live, see WORKLOG |
| 5 | CI promote job with the production Environment; measure commit to serving | S–M | **Done** — verified live via PR #4, see WORKLOG |
| 6 | `rollback.yml` plus all three failure scenarios executed | M | **Done** — verified live, see WORKLOG |
| 7 | Issue write-ups, `deploy/README.md`, WORKLOG, AI_WORKFLOW | M | **Done** — `docs/issues/021-024`, `deploy/README.md` and `WORKLOG.md` updated throughout this Part; AI_WORKFLOW session note appended |

Step 3 sits before step 4 deliberately: the E2E is the highest-value and highest-risk piece, it is fully provable on the cluster that already exists, and it stands on its own even if the prod cluster never happens.

## Step detail

### Step 1 — CI lint + test

Triggers `pull_request`, `push: [main]`, `workflow_dispatch`; `concurrency` with `cancel-in-progress`; `permissions: contents: read`. Two parallel jobs on `ubuntu-latest`: `ruff check .` and `pytest -q`, both with `working-directory: app`. `astral-sh/setup-uv` pinned to **0.12.5** to match the Dockerfile, `enable-cache: true`, `cache-dependency-glob: app/uv.lock`. Actions pinned by commit sha.

`.github/dependabot.yml` lands here too — `github-actions` and `docker` ecosystems, weekly. It is what makes the digest-pinning in step 2 and the base-image pinning in N5 sustainable rather than a slow slide into staleness.

Chart linting is deferred to step 3, which is where the first chart change lands — wiring it now means rewriting it twice.

Branch protection on `main` requiring these two checks is configured in the GitHub UI (operator action, not repo state).

**Verification.** Draft PR from the feature branch; `gh run watch`, `gh run view --log`. Expect `All checks passed!` and `106 passed`. Second run to show a warm uv cache. Then a scratch commit that breaks one test, to prove the job actually goes red, dropped afterwards. Run URLs recorded.

### Step 2 — CI build, scan, sign, push

`needs: [lint, test]`, `permissions: {contents: read, packages: write, id-token: write}`. The `id-token` permission is what makes cosign keyless signing possible.

Login with `GITHUB_TOKEN`; buildx; two images from `app/`; `platforms: linux/amd64`; `sbom: true`, `provenance: mode=max`. Tag `ghcr.io/idanbro/<image>:${GITHUB_SHA:0:7}`. No `latest`, no moving tags. `type=gha` cache with a distinct scope per image. `build-args: GIT_SHA=${{ github.sha }}` — the **full** sha, because `org.opencontainers.image.revision` is conventionally full; `deploy.sh` changes to match so dev and CI images carry identical labels. Digests captured as job outputs for step 5.

Then Trivy against the pushed digest, `severity: HIGH,CRITICAL`, `ignore-unfixed: true`, `exit-code: 1`, with an initially empty `app/.trivyignore` for documented exceptions. Then `cosign sign --yes <repo>@<digest>`, keyless.

**One manual action, mine to request and the operator's to perform:** after the first successful push, flip both GHCR packages to public in the GitHub UI. Packages are created private even from a public repo, and the prod cluster will `ImagePullBackOff` otherwise.

**Verification.** `docker pull ghcr.io/idanbro/pokeproxy@sha256:<digest>` anonymously from WSL; `docker inspect` to confirm the revision label equals the commit; `cosign verify` against the published digest with the expected OIDC issuer and identity; `docker buildx imagetools inspect` to confirm the SBOM and provenance attestations are attached; second run compared for a warm buildx cache. A deliberate `.trivyignore` removal to confirm the scan gate can actually fail the job.

### Step 3 — E2E check

**Script** (`app/e2e/e2e_check.py`). Args `--proxy-url`, `--mock-url`, `--timeout`, `--retries`; secret from `POKEPROXY_HMAC_KEY`. Plain Python with explicit failures — the app image is built `--no-dev`, so pytest is not available. Emits one JSON summary line so it reads cleanly beside the app's JSON logs.

A unique payload per run is mandatory, not stylistic: `number` randomised in 900000-999999 and `name` set to `e2e-<uuid4[:8]>`. Dedup suppresses a repeated payload *before* rule evaluation, so a fixed payload would fail the check against a perfectly healthy deployment.

| # | Assertion | What it proves |
|---|---|---|
| 1 | signed matching payload gives `200 {"status":"received"}` | edge, HMAC, forward |
| 2 | that exact name present in mock `/received` with `reason: strong fire pokemon` | rule matching and real delivery, not merely a 200 |
| 3 | signed non-matching payload gives `200 {}` and is **absent** from `/received` | rules are actually evaluated |
| 4 | corrupted signature gives `401` | HMAC validation is live |

Assertion 2 needs no polling because the forward is synchronous (verified above); a 2 s bounded retry stays in as cheap insurance.

**Image** (`app/Dockerfile.e2e`). `ARG BASE_IMAGE`, `FROM ${BASE_IMAGE}`, `COPY e2e/ /app/e2e/`, `USER 10001`, `ENTRYPOINT ["python","/app/e2e/e2e_check.py"]`. CI builds it after pushing pokeproxy with `BASE_IMAGE=ghcr.io/idanbro/pokeproxy:<sha>`; `deploy.sh` builds it locally with `BASE_IMAGE=pokeproxy:<sha>`.

**Job template.** Gated on `.Values.e2e.enabled`. Carries **both** hook dialects — `helm.sh/hook: post-install,post-upgrade` and `argocd.argoproj.io/hook: PostSync`, each with its delete policy. Verified safe: Argo CD ignores all Helm hooks once any Argo hook annotation is present, so this is one execution per path, never two. Note for the future: adding the Argo annotation means any *later* Helm hook in this chart silently stops running under Argo CD.

`backoffLimit: 0` with retries inside the script, so all output lands in one pod's logs. `activeDeadlineSeconds: 120` so a hang cannot wedge a sync. `ttlSecondsAfterFinished: 3600` as a backstop so failed Jobs do not accumulate — the hook delete policies handle the normal case, the TTL handles the case where they do not. Full PSA-restricted securityContext, `enableServiceLinks: false`, `restartPolicy: Never`, `envFrom` the `pokeproxy-hmac` Secret, label `app.kubernetes.io/component: e2e`.

**Accepted risk, stated rather than left implicit:** the E2E Job mounts the real HMAC signing key. The app validates against a single key, so a dedicated test credential is not possible without a protocol change (see M3 in the known-gaps write-up). The blast radius is a Job in the same namespace that already hosts the Secret.

**NetworkPolicy — exactly three additions.** pokeproxy needs no new rule, because E2E traffic arrives *from Traefik*, which `allow-ingress-to-pokeproxy` already permits.

| Rule | Direction |
|---|---|
| `allow-e2e-egress-to-ingress` | e2e to kube-system :80 |
| `allow-e2e-egress-to-mock-downstream` | e2e to mock :8001 |
| `allow-mock-downstream-ingress-from-e2e` | mock from e2e :8001 |

Known difference from a real client, worth stating: an in-cluster request to Traefik's ClusterIP skips the k3d load balancer that an external request traverses. Everything from Traefik inward — routing, the body-size middleware, the app — is identical.

**Verification, all on the dev cluster, before any prod cluster exists.**

| Check | Expected |
|---|---|
| `helm lint --strict`, `helm template` | Job renders only when `e2e.enabled` |
| `deploy.sh` with e2e on | hook Job runs, four assertions pass, logs captured |
| run twice back to back | passes both times — proves the dedup handling is real |
| point the e2e at a wrong mock URL | Job fails, `helm upgrade --atomic` auto-rolls-back; `helm history` captured |
| delete one of the three NetworkPolicy rules | Job fails on **connection refused** (not a timeout — this cluster's NetworkPolicy controller actively rejects rather than silently dropping, corrected from this plan's original assumption) — the same A/B rigour as Part 2 step 8; proves the rules are load-bearing |
| `/received` length | grows by exactly 1 per passing run |

`helm lint --strict` and `helm template | kubeconform -strict` join CI in this step. kubeconform needs `-ignore-missing-schemas` or an explicit schema location for the `SealedSecret` CRD; the chart is linted against the same values files Argo CD uses, so CI validates what actually deploys rather than something adjacent to it.

### Step 4 — Prod cluster and Argo CD

`bootstrap-prod.sh`, idempotent, and the order matters: create cluster if absent, apply `namespace.yaml` with its PSA labels, generate and apply `.secrets/sealing-key-prod.yaml` **before** the controller, install sealed-secrets (same pinned version, `keyrenewperiod=0`), install Argo CD from `install-values.yaml`, apply the Application, print the admin password and a port-forward hint. `KUBE_CONTEXT` pinned on every cluster-touching call: the S5 lesson, applied from the start this time.

`values-local.yaml` and `values-prod.yaml` move to `deploy/envs/{local,prod}/values.yaml`. `scripts/deploy.sh` and `scripts/seal-hmac.sh` change one path each — `helm -f` has never required a values file inside the chart. `seal-hmac.sh` also gains `--env {local,prod}` and the **N7 fix**, which stops being cosmetic here: the target file will hold image tags and digests, so a wholesale `cat >` becomes data loss. Targeted key replacement via yq.

The Application: `repoURL https://github.com/IdanBro/PokeProxy`, `targetRevision main`, `path deploy/helm/pokeproxy`, `helm.valueFiles: ["../../envs/prod/values.yaml"]`, `syncPolicy.automated` with `prune` and `selfHeal`, and an explicit **`retry.limit: 3` with exponential backoff**. That bound matters: with `selfHeal` on and a PostSync hook that keeps failing, an unbounded retry would re-sync and re-run the E2E against a broken deploy indefinitely. Whether `selfHeal` can still re-trigger past the retry limit is a live behaviour I intend to observe in scenario B rather than assume. The namespace is deliberately not owned by Argo.

`deploy/envs/prod/values.yaml`: GHCR repositories, tag and digest seeded once by hand from step 2 then owned by CI, Traefik ingress, `mock-downstream.enabled: true` (the E2E's downstream assertion needs a sink; an external production would need a synthetic sink instead — documented as the delta), `e2e.enabled: true`, HMAC sealed against the prod cluster's own key.

**Verification.**

| Check | Expected |
|---|---|
| `bootstrap-prod.sh` from scratch | cluster, sealed-secrets, Argo CD and Application, one command |
| Argo CD status | Synced / Healthy, 4/4 pods |
| `kubectl describe pod` | image reference is `ghcr.io/...@sha256:...` — pulled from the registry, not imported, and the provenance-carrying index digest resolves correctly |
| PostSync Job | ran and passed — this is where the hook-annotation behaviour gets confirmed in practice |
| both clusters | dev still serving on 8080, prod on 8081, contexts distinct |
| signed request to `localhost:8081/stream` | 200 |
| hand-edit a live Deployment | selfHeal reverts it — proves continuous reconciliation, not just first apply |
| dev path after the values move | `deploy.sh` still deploys cleanly from `deploy/envs/local/values.yaml` |

### Step 5 — CI promote

`needs: [build-pokeproxy, build-mock-downstream, build-e2e]` as shipped (the design said `needs: [scan, sign]`, but scan and sign are steps inside the build jobs, not jobs of their own), `if: github.ref == 'refs/heads/main'`, `permissions: contents: write`, `environment: production` for the deployment record, `concurrency: promote` so two merges cannot race. yq writes six fields — tag and digest for pokeproxy, mock-downstream and e2e — into `deploy/envs/prod/values.yaml`. Commits as `github-actions[bot]`, subject `chore(deploy): promote <sha>`, digests in the body, `git pull --rebase` then push.

With branch protection now on (D11), this job needs a bypass. **Corrected after implementation:** the ruleset exemption this paragraph originally proposed is impossible here — GitHub rejects an `Integration`-type bypass actor on a user-owned repo (*Actor GitHub Actions integration must be part of the ruleset source or owner organization*). What shipped is a fine-grained PAT (`Contents: Read and write`, this repo only) fed to `actions/checkout`'s `token:` input, plus `[skip ci]` on the promote commit, because a PAT push is *not* exempt from retriggering CI the way `GITHUB_TOKEN` is. Full account in `WORKLOG.md` and `deploy/README.md`.

**Verification.** Merge a trivial app change and follow the whole chain. Confirm the promote commit did **not** trigger a second CI run. Confirm the digest in git equals `kubectl get pod -o jsonpath=...` on the prod cluster. Measure and record **commit to serving** in seconds; that number belongs in the README as a measurement, not a claim. Framed against DORA where it maps cleanly: this is lead time for changes, and step 6's scenarios exercise change failure rate and MTTR.

### Step 6 — Rollback

`rollback.yml`, `workflow_dispatch` with input `sha`. Resolves that sha's digests from GHCR via `docker buildx imagetools inspect`, so the operator supplies only a sha. Writes tag and digest, commits `revert(deploy): roll back to <sha>`, pushes. Argo reconciles within the poll interval.

Three scenarios, executed rather than described:

| Scenario | Setup | Expected |
|---|---|---|
| A — rollout failure | promote an image that crashes on boot | new pods never Ready, old pods keep serving, Argo Degraded, **zero failed requests** measured with the load generator against 8081 throughout |
| B — verification failure | promote an image that boots healthy but forwards wrongly | pods Ready, PostSync E2E fails, sync Failed; **measure the exposure window**; observe whether the retry limit actually bounds the re-sync loop; roll back and confirm recovery |
| C — bad version found later | run the workflow against a healthy deployment | clean revert to the prior sha |

The broken images are built from a scratch branch via `workflow_dispatch`; nothing broken gets merged to `main` to stage a demo.

### Step 7 — Documentation

Issue write-ups per `docs/issues/TEMPLATE.md` for every real issue fixed — at minimum S4 and N7, plus whatever the steps uncover. `deploy/README.md` gains a GitOps section, the prod bootstrap procedure, and the external-production delta. `WORKLOG.md` Current State and Backlog. Session note appended to `AI_WORKFLOW.md`.

## Verification and rollback

### Where verification actually sits

With no pre-promotion gate, a bad version reaches prod pods before anything functional checks it. Being precise about which failures actually escape matters more than the general worry:

| Failure class | Reaches users? | Why |
|---|---|---|
| Image will not pull, container crashes, probes fail | **No** | `maxUnavailable: 0` plus readiness — new pods never join the Service. Proven in Part 2 step 9: rollout restart under 30 rps, 2487 requests, 0 errors |
| Known HIGH/CRITICAL vulnerability in the image | **No** | Trivy fails the build before the promote job runs |
| Unrenderable chart or invalid manifest | **No** | `helm lint --strict` and `kubeconform` in CI |
| Healthy pods, functionally wrong (bad rules, broken HMAC, downstream never called) | **Yes**, for the duration of the E2E | exactly the class the PostSync E2E exists to catch — seconds, not minutes |

So the uncovered class is precisely one — healthy-but-wrong, exposed for the length of one E2E run. That is the honest cost of deferring the ephemeral pre-promotion cluster, and it is a cost I can state and defend rather than one I have to discover.

A PostSync hook **detects**; it does not **prevent**. Describing it as a gate would be describing detection as prevention.

### Rollback

| Failure | Detection | Response | Automated? |
|---|---|---|---|
| Rollout failure | probes; Argo health goes Degraded | old ReplicaSet keeps serving; nothing to do | yes, structurally |
| PostSync E2E fails | Argo sync Failed, app Degraded | `gh workflow run rollback.yml -f sha=<last-good>` | detection automatic; response one command |
| Bad version found later | humans, or Part 4 alerts | same path | same |

**Rollback is a pure image swap, and that is worth stating explicitly** because it is the first thing anyone should ask. There is no database, no schema migration, and no persistent state: Redis is a best-effort cache the service already degrades past (Part 1 C4, proven again in the Part 2 audit by scaling Redis to zero with zero 5xx). Rolling backwards is exactly as safe as rolling forwards.

**One real caveat, and it is not obvious.** The cache stores *downstream responses* keyed by payload hash, with `CACHE_TTL_SECONDS=300`. If a bad version forwarded to the wrong place and cached the wrong response, a repeat of that payload replays the poisoned entry for up to five minutes **after** the rollback completes. So a rollback restores correct behaviour for new payloads immediately and for previously-seen payloads only after the TTL expires. Where correctness matters more than cache warmth, `redis-cli FLUSHALL` belongs in the rollback runbook as an optional step. This is also exactly the trap that made a Part 2 step 9 revert look like it had failed when it had actually worked.

**`helm rollback` does not exist under Argo CD.** Argo renders and applies; it keeps no Helm release history. Part 2's `--atomic` and `helm history` story applies only to the `scripts/deploy.sh` dev path. Under GitOps the rollback mechanism is a git revert. `argocd app rollback` exists but suspends auto-sync, making it a break-glass tool rather than the story.

A pleasant asymmetry worth being able to explain: the same Job under `helm upgrade --atomic` gives Helm-native **automatic** rollback on hook failure. The dev path therefore has stronger automatic rollback than the GitOps path — a concrete example of what GitOps trades away for auditability.

### Documented, not built — automatic revert

Argo CD Notifications can POST to GitHub's `repository_dispatch` endpoint on sync failure, triggering `rollback.yml` unattended: bad deploy, E2E fails, revert commit, Argo redeploys the previous version, no human. It is egress-only, so it works from a laptop cluster.

Not building it: it needs a GitHub write token living inside the cluster, and it converts a flaky test into an automatic production change. A false alarm that reverts a healthy deploy is usually worse than a human spending thirty seconds looking at it.

## What is deliberately deferred

**An ephemeral k3d cluster inside the CI runner**, deploying the just-built image and running the same E2E before promotion. This is the strongest available gate and would move verification upstream of the cluster entirely. Deferred by decision, not oversight — it is additive to everything here (the E2E already runs as a Job and is already URL-parameterised, so wiring it in later is a workflow job, not a redesign).

## Constraints this sets for later Parts

- **Part 4's monitoring stack has to pick a cluster.** Argo CD and the GitOps flow live in `k3d-pokeproxy-prod`; dev is the throwaway. Probably prod, decided then.
- **Part 4's dashboards should read from the prod cluster** if the E2E is generating the only reliable synthetic traffic there.
- **Part 5's one-command bootstrap composes `bootstrap-prod.sh` and `deploy.sh`** rather than replacing them; both are being written to be idempotent and context-pinned for exactly that reason.
- **There is still no root `README.md`.** Deliverable 8 asks for one. Part 5's scope, noted here so it is not forgotten.
- **GHCR retention.** Three images per commit, forever, with no cleanup policy. Fine at this scale, wrong at any real one.
- **New backlog opened by this Part:** mock-downstream's `received_pokemon` is an in-process list that grows forever, and every E2E run appends to it on a long-lived prod cluster. The prod sealing key is gitignored, so a fresh clone cannot reconstitute the prod cluster's secret — the same accepted trade-off already documented for dev. Argo CD admin credentials need a handling decision.

## Definition of done for Part 3

Status as of the 2026-08-23 requirement audit (below). Evidence for every **Done** row is in `WORKLOG.md`.

| # | Item | State |
|---|---|---|
| 1 | CI green on real GitHub Actions across lint, test, build, scan, sign and promote, run URLs recorded | **Done** — runs [32640298921](https://github.com/IdanBro/PokeProxy/actions/runs/32640298921) (6/6) and [32645019846](https://github.com/IdanBro/PokeProxy/actions/runs/32645019846) (promote 7/7) |
| 2 | Branch protection on `main` requires the CI checks | **Not done** — `required_status_checks` carries no contexts, so a red PR can still merge. Not re-verified during the audit (`gh` is not on PATH in that shell); rests on WORKLOG's record |
| 3 | Three images in GHCR at short-sha tags, anonymously pullable by digest, with SBOM + provenance, `cosign verify`-able | **Done** — checked with a locally installed `cosign v3.1.3` and `buildx imagetools inspect`, not by trusting the CI step's exit code |
| 4 | The Trivy gate proven able to fail the job, not merely present | **Done** — its first real run failed on genuine HIGH CVEs (`setuptools` CVE-2025-47273; `starlette` CVE-2026-48818 / CVE-2026-54283) |
| 5 | Prod cluster reconciled by Argo CD from `main`, pods verifiably running GHCR digests | **Done** — `spec` and `status.imageID` both digest-pinned |
| 6 | PostSync E2E sends real protobuf and HMAC through the ingress and validates the mock downstream result, passing, with logs captured | **Done** — read from Argo's own `operationState`, corroborated by the unique record in the mock's `/received` |
| 7 | The dev path still works after the values move | **Done** — render byte-identical before/after; `deploy.sh` green at revision 11 |
| 8 | Commit to serving measured and recorded | **Done, with a stated caveat** — 155s, but Argo's refresh was force-triggered rather than left to the 30s poll. The passive-path number is still unmeasured |
| 9 | All three rollback scenarios executed with captured output | **Done, live, 2026-08-23** — A, B and C all executed with captured evidence. See WORKLOG ("Step 6 scenarios A and B", "Scenario C — executed live") |
| 10 | S4 and N7 closed with write-ups | **Done** — `docs/issues/021-values-prod-undeployable.md`, `022-seal-hmac-wholesale-rewrite.md`; plus `023` (F-2 sealing-key redesign) and `024` (branch-protection/PAT) written up the same day |
| 11 | Anything not verified by execution is labelled as not verified | **Held** — including in this table |

## Requirement audit — 2026-08-23

One hypothetical commit traced end to end against `README_HOME_ASSIGNMENT.md` Part 3. Findings recorded here and in `WORKLOG.md`; nothing was changed in response to them yet. Part 4 observability was deliberately out of scope.

### The trace

| Stage | Mechanism | Evidence | Verdict |
|---|---|---|---|
| PR opened | `ci.yml` `on: pull_request` → lint, test, chart-lint, build ×3 | `.github/workflows/ci.yml:1` | real, runnable, verified live |
| Merge gate | branch protection: a PR is required, but **no required check contexts** | WORKLOG, not re-verified | gap — DoD #2, F-11 |
| Push to `main` | same workflow; buildx → GHCR `:<short-sha>`, digest captured as a job output | `ci.yml:79`, `:125`, `:171` | real |
| Scan + sign | Trivy by digest after push; cosign keyless via OIDC | `ci.yml:110`, `:121` | real; gate proven able to fail |
| Chart validation | `helm lint --strict` + kubeconform over both env values files | `ci.yml:51` | real, but **gates nothing** — F-4 |
| Desired-state change | `promote`: yq writes six tag/digest fields, commits `[skip ci]` via PAT, pushes to `main` | `ci.yml:215` | real, verified live |
| Reconcile | Argo CD Application, `targetRevision: main`, 30s poll, `prune` + `selfHeal`, `retry.limit: 3` | `deploy/argocd/application.yaml` | real, verified live |
| Rollout | `maxUnavailable: 0` plus readiness probe | `deploy/helm/pokeproxy/templates/pokeproxy/deployment.yaml:13` | real, verified in Part 2 step 9 |
| Post-deploy E2E | PostSync Job: real protobuf + HMAC through Traefik, asserts the mock received it | `templates/e2e/job.yaml`, `app/e2e/e2e_check.py` | real, verified — but detects, does not gate (F-3) |
| E2E failure handling | Argo marks the sync Failed, retries ×3 with backoff, then stops | `application.yaml` | bounded, but no notification and no automated response |
| Rollback | `rollback.yml`, `workflow_dispatch` | **file does not exist** | **not runnable** — F-1 |

### BLOCKER

**F-1 — `.github/workflows/rollback.yml` does not exist.** The rollback stage is named in this document's pipeline diagram, in its rollback response table (`gh workflow run rollback.yml -f sha=<last-good>`), in `deploy/README.md` and in `WORKLOG.md`'s stack summary. The assignment requires every stage to be "real, runnable, and documented"; this one is documented only. Scenarios A/B/C are also unexecuted. This is step 6, correctly tracked as Not started — recorded here as the top blocker for Part 3, not as a surprise.

**F-2 — the prod HMAC secret cannot be reconstituted on any machine but this one, and under GitOps that is fatal rather than inconvenient.** `.secrets/` is gitignored. On a clean clone, `seal-hmac.sh --env prod` mints a new key (`scripts/seal-hmac.sh:110`) and re-seals `deploy/envs/prod/values.yaml` **in the working tree** (`scripts/seal-hmac.sh:157`). Argo CD reads the *committed* file from GitHub, whose ciphertext was sealed against a key that cluster does not have, so the controller cannot decrypt it, `pokeproxy-hmac` is never created, `envFrom.secretRef` is not optional (`templates/pokeproxy/deployment.yaml:48`), pods stick in `CreateContainerConfigError`, the Application never reaches Healthy, and `bootstrap-prod.sh` exits 1 after 600s.

`WORKLOG.md`'s backlog calls this "the same accepted trade-off already documented for dev." **That framing is wrong.** In dev, Helm reads the re-sealed working tree, so the re-seal works. In prod, Argo reads git, so it cannot. Different failure mode, different severity. It blocks a reviewer reproducing the GitOps demo and it blocks Part 5's clean-machine one-command bootstrap. Candidate fixes: have `seal-hmac.sh --env prod` refuse to mint a fresh key while the committed values file already carries a ciphertext, telling the operator to restore the key or re-seal **and commit**; or commit the public cert half so sealing does not require a live cluster.

**F-3 — post-deploy verification does not gate the deployment, and today nothing responds when it fails.** The assignment asks for a check that "gates the deployment on it." As built the E2E is a PostSync hook: pods are already serving before it runs, and on failure Argo marks the sync Failed, retries ×3, then stops — no notification, no revert, and (per F-1) no rollback workflow. The detect-not-prevent trade is deliberate and argued above under "Where verification actually sits"; what is missing is that its only documented compensating control does not exist yet.

### SHOULD FIX

**F-4 — `chart-lint` gates nothing.** It declares no `needs` and no job needs it; `promote` depends only on the three build jobs (`ci.yml:217`). A commit that breaks `helm lint --strict` or kubeconform still promotes and still reaches Argo. This document's failure-class table claims "Unrenderable chart or invalid manifest → reaches users? **No**" — **false as implemented.** One-line fix: add `chart-lint` to `promote.needs`.

**F-5 — the promoted desired state is never linted.** The promote commit carries `[skip ci]` (`ci.yml:265`), so the exact `deploy/envs/prod/values.yaml` that Argo consumes never passes through chart-lint; chart-lint only ever sees the pre-promote file. Low blast radius today (six scalar fields) but it compounds F-4: nothing validates the artifact Argo actually reads. Fix: render + kubeconform inside the promote job after the yq write, before committing.

**F-6 — workflow-level `cancel-in-progress: true` also applies to `main`.** `ci.yml:9` groups on `ci-<workflow>-<ref>`, so two merges in quick succession cancel the older run — including a `promote` job in flight. The job-level `concurrency: {group: promote, cancel-in-progress: false}` (`ci.yml:220`) stops two promotes interleaving but cannot stop the run being cancelled outright. Usually benign, since the newer commit promotes anyway, but a cancellation between `git commit` and `git push` silently skips that sha's promotion. Fix: `cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}`.

**F-7 — the designed rollback covers only the image axis; Argo reconciles everything under `deploy/`.** Step 6 specifies writing tag+digest for a target sha, which does not roll back a regression introduced by a chart or values change (probes, NetworkPolicy, resources, rules). The rollback prose in this document says the mechanism is "a git revert" — a different mechanism from what step 6 specifies. Pick one, or implement both and state when each applies.

**F-8 — `activeDeadlineSeconds: 120` sits barely above the E2E script's own 90s startup budget** (`templates/e2e/job.yaml:20` vs `app/e2e/e2e_check.py:30`). On the Helm path — where the hook fires before the Deployments are Ready, the race step 3 already found — an 85s rollout leaves the assertions 30s. A slow-but-healthy deploy then fails the E2E and reads as a bad deploy. Make the deadline a function of the startup budget, or shorten the startup wait on the Argo path, where PostSync already implies readiness.

**F-9 — the E2E Job declares no resource requests or limits**, unlike every other workload in the chart. Inconsistent with Part 2's stated posture and unbounded on a saturated node.

**F-10 — signing is write-only.** All three images are cosign-signed and nothing verifies at pull time; the cluster pulls unverified digests, and Argo CD's `signatureKeys` covers git commit signatures, not images. `cosign verify` was run by hand once (WORKLOG). Either say plainly in `deploy/README.md` that signatures are provenance verified out-of-band, or add an admission policy — as drawn, the chain has no consumer.

**F-11 — a red PR can still merge to `main`** (DoD #2). The pipeline itself stays safe, since `promote` needs the build jobs and those need lint and test, but broken code lands on `main` with no promotion — a confusing half-state to debug.

### NICE TO HAVE

**F-12** — this document's "Final pipeline" diagram and step-5 text were stale in three ways (recursion mechanism, `needs:`, the ruleset bypass). Corrected in place above.

**F-13** — `docs/issues/021+` for S4 and N7 do not exist; both are recorded as prose in `WORKLOG.md`'s backlog table instead of the per-issue format deliverable 2 asks for. Step 7.

**F-14** — nothing asserts that the digests promote writes are actually pullable. The build jobs' outputs make this near-certain; a `buildx imagetools inspect` in the promote job makes the desired state self-validating for about three seconds of runtime.

**F-15** — `already_sealed()` returns success without confirming that the existing ciphertext decrypts under the reused key, so a stale key/ciphertext pair is accepted silently and only surfaces as a failed pod. Same family as F-2.

**F-16** — `git pull --rebase origin main` in promote (`ci.yml:266`) has no conflict handling; `concurrency: promote` makes a race improbable rather than impossible, and a conflict would fail opaquely.

**F-17** — fork PRs cannot run `build-*`: `secrets.GITHUB_TOKEN` on a fork PR has read-only `packages`. Irrelevant for a single-author repo, worth one sentence.

**F-18** — the branch-protection/PAT episode is one of the more interesting real findings in Part 3 and has no `docs/issues/` write-up.

## Requirement audit — 2026-08-24 (second pass, live re-verification)

Same trace repeated a day later, against `origin/main` at `da55fc1` (the promote following PR #7). Unlike the 2026-08-23 pass, every claim below is read from a live system in this session — `gh`, `kubectl`, `cosign`, `docker buildx imagetools` via WSL, not from `WORKLOG.md`'s prior record. Purpose: catch drift or regressions between "fixed and merged" and "actually still true." Part 4 observability out of scope, per instruction.

| Stage | Live check this session | Result |
|---|---|---|
| Merge gate | `gh api .../branches/main/protection` | `required_status_checks.contexts = ["Chart lint","Lint","Test"]`, `strict:true` — F-11 still closed |
| CI on the actual last merge | `gh run list` | push to `main` @ `5966025` → run [32668762523](https://github.com/IdanBro/PokeProxy/actions/runs/32668762523), **success** |
| Image publication | `docker buildx imagetools inspect ghcr.io/idanbro/pokeproxy@sha256:87a5a28e…` (anonymous) | resolves, OCI index, pullable |
| Signing | `cosign verify` against that digest, expected OIDC issuer/identity | verified, transparency-log claim confirmed |
| Desired-state change | current `deploy/envs/prod/values.yaml` | tag/digest = `5966025` / `sha256:87a5a28e…`, matches the promote commit body |
| Reconcile | `kubectl -n argocd get application pokeproxy` | `Synced Healthy da55fc1…` — the **exact latest commit**, not a stale prior sync |
| Rollout | `kubectl get pods -o jsonpath` on both prod pods | both `Running`, image = `ghcr.io/idanbro/pokeproxy@sha256:87a5a28e…`, byte-for-byte match to git |
| Serving | `curl -X POST localhost:8081/stream` | `401` — live, correct |
| Rollback path | `rollback.yml` exists, last real run [32666881696](https://github.com/IdanBro/PokeProxy/actions/runs/32666881696) success | unchanged from 2026-08-23, not re-executed this pass (would mutate prod for no new information) |
| F-8 timing margin | `app/e2e/e2e_check.py:30` `STARTUP_MAX_WAIT_SECONDS = 90` vs `job.yaml:23` `activeDeadlineSeconds: 180` | 90s margin for assertions after startup, as fixed |

**Conclusion: no regressions.** Every F-1–F-18 fix from the 2026-08-23 audit still holds, verified against running state rather than re-read from `WORKLOG.md`. The pipeline this document describes is, right now, the pipeline actually running.

**One new finding, doc-only:**

**N1 — `deploy/README.md`'s Rollback section (lines 180–182) is stale and contradicts this plan's own DoD table.** It reads "Not yet run against a real failure" and describes F-7 as "not yet executed live" for all three rollback scenarios. In fact, scenarios A, B and C were all executed live on 2026-08-23 with captured evidence (`WORKLOG.md`, "Step 6 scenarios A and B" / "Scenario C — executed live"), and this document's own DoD item 9 already reads **Done**. The PR that closed step 7 ([#7](https://github.com/IdanBro/PokeProxy/pull/7)) updated `WORKLOG.md`, issue write-ups and `AI_WORKFLOW.md` but missed this section of `deploy/README.md`. Severity: **NICE TO HAVE** — no functional impact, but deliverable 8 is a README a reviewer reads to understand the submission, and it currently understates what was actually proven. **Fixed** — `deploy/README.md`'s Rollback section now summarizes all three executed scenarios with results, in place of the stale "not yet executed" paragraph.
