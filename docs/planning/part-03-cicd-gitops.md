# Part 3 — CI/CD & GitOps

## What I am actually solving

Part 2 made the service deployable. Every deployment so far has been a human running `scripts/deploy.sh` on their own laptop against a cluster they created by hand. Nothing gates `main`, no artifact is published anywhere, and the only proof a deploy worked is that someone ran `curl` afterwards and looked at the output.

Part 3 turns that into a pipeline: a commit is linted and tested, becomes an immutable published image, becomes a change to desired state in git, and is reconciled into a cluster by an agent — then verified by real signed protobuf traffic before anyone calls it done.

Two Part 2 items close here:

| ID | Gap | Closes in |
|---|---|---|
| S4 | `values-prod.yaml` is undeployable on two counts — rules point at the mock Service it disables, and the egress NetworkPolicy blocks any external downstream | step 4 (rewritten as the GitOps desired state) |
| N7 | `scripts/seal-hmac.sh:96` rewrites `values-local.yaml` wholesale with `cat >`, discarding anything else in the file | step 4 (mandatory — the file will hold image tags and digests) |

And one failure class disappears structurally:

**Sha-drift**, recorded three separate times in Part 2 (steps 6, 7, 8). A session gap between "build the image" and "deploy it" leaves the cluster running an image tagged for an older sha, and `k3d image import` succeeds either way, so it only surfaces as `ImagePullBackOff` on the next deploy. Once the cluster can only run a digest that CI published, this becomes impossible rather than merely unlikely.

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
| D8 | `ruff format --check` is **not** added | measured: it would reformat 13 of 27 files. A formatting sweep would bury real history and buys nothing operationally. On record as a decision |
| D9 | Env values files **stay inside the chart directory**; `values-prod.yaml` is *rewritten*, not deleted | Argo CD rejects Helm `valueFiles` resolving outside the Application's `path`. A `deploy/envs/` layout would need a multi-source Application for no benefit at this size |

### On D3 — why a commit sha is not enough on its own

The commit sha is immutable. The *tag* is not: `ghcr.io/idanbro/pokeproxy:a1b2c3d` is a rewritable label pointing at a manifest digest. Naming it after a commit buys uniqueness and traceability by convention, not enforcement.

The realistic way it breaks: re-run the build job for a commit (flaky runner, expired token), the base image `python:3.13-slim-bookworm` has moved underneath us since — that is N5 — so the rebuild produces different bytes under the same tag. With `imagePullPolicy: IfNotPresent`, a node that already pulled keeps the old layers and a node that scales up later gets the new ones. Two builds serving under one version string, with `kubectl describe` showing the same tag on both.

The digest is the hash of the manifest itself, so it cannot point at different bytes. Cost of pinning it is roughly two lines of Helm and reading a value CI already produces.

### On D9 — what was wrong with `values-prod.yaml`

It lints clean, renders clean, and would not work:

1. It sets `mock-downstream.enabled: false` but the routing rules still resolve to the mock's Service address. The app would start and then fail every forward, pointing at a Service that does not exist. Nobody filled in real URLs because there is no real downstream.
2. `allow-pokeproxy-egress-to-dependencies` permits egress only to Redis and the mock. A genuinely external downstream would be blocked at the pod even with correct URLs.

Rewriting it as the deployable GitOps desired state fixes both by making it describe an environment that actually exists. The external-production delta — real URLs, a widened egress rule, nginx ingress — goes into `deploy/README.md` as prose, because prose cannot be deployed and broken.

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
| Desired state | `deploy/helm/pokeproxy/values-prod.yaml` in the same repo | one clonable artifact; `GITHUB_TOKEN` pushes do not trigger workflows, so recursion is structurally prevented |
| Verification | one Python script, one derived image, run as a hook Job | identical logic under Helm locally and Argo CD in prod |
| Value editing in CI | **yq**, pinned | `sed` on YAML is how you lose a sealed ciphertext |

## Alternatives I considered

**Flux instead of Argo CD.** Genuinely the stronger rollback story: `HelmRelease.spec.test` plus `upgrade.remediation` gives *automatic* rollback when the post-deploy test fails, which Argo CD does not do natively. Its image-automation controller would also update the tag in git by itself, so CI would not need write access to the repo at all.

Chose Argo CD anyway, for three reasons. The assignment names it. Its UI makes "who reconciles what" demonstrable in a way `flux get` does not. And the substantive one: automatic remediation matters most when post-deploy verification is the *only* gate — the moment an ephemeral pre-promotion cluster exists (see "What is deliberately deferred"), verification moves upstream of the cluster and auto-remediation becomes a nice-to-have. If someone pushes back on this, Flux is the answer I would switch to, and nothing in the chart depends on Argo.

**A separate config repository.** The correct shape at scale: independent RBAC, deploy history not interleaved with app history, no recursion question at all. Rejected because the submission has to be one clonable artifact, and the recursion risk that normally justifies the split is already handled — commits pushed with the default `GITHUB_TOKEN` do not trigger `push` workflows.

**A self-hosted runner on this laptop.** Would let CI deploy directly and would be far less machinery. Rejected outright: cluster state would become a side effect of a job, git would never describe what is running, and calling it GitOps would be false.

**An ephemeral k3d cluster inside the CI runner as a pre-promotion gate.** My original recommendation, and still the strongest available gate — it blocks a bad image from ever entering desired state. Deferred deliberately (see below). Its absence is the main honest weakness of this design and is quantified under "Verification and rollback".

**Digest-only pinning, no tag.** Strictly the purest form, and rejected on operability: `git log -p` on desired state becomes forty characters of hex with nothing to tell a human which commit shipped. Carrying both costs one extra field.

**Argo CD creating the namespace** via `CreateNamespace=true` plus `managedNamespaceMetadata`. Rejected — `deploy/k8s/namespace.yaml` already carries the PSA labels and was added specifically because the namespace was untracked (B2). Two owners for one object is how those labels silently drift.

## Final pipeline

```
 push to main
      |
      v   GitHub Actions - never touches a cluster
 +-----------------------------------------------------------+
 | lint      ruff check | helm lint --strict | kubeconform    |
 | test      pytest (106)                                     |
 |   +--> build    buildx x3 -> GHCR :<short-sha>, capture digests
 |          +--> promote   yq-write tag+digest into values-prod.yaml,
 |                         commit as github-actions[bot], push
 +-----------------------------------------------------------+
      |   (GITHUB_TOKEN push => no workflow recursion)
      v
 Argo CD in k3d-pokeproxy-prod, polling main every 30s
      |-- sync: helm template + apply
      |-- rollout: maxUnavailable 0 + probes
      +-- PostSync Job: real protobuf + HMAC through Traefik,
                        assert the payload reached mock /received
             pass -> Synced / Healthy
             fail -> Sync Failed, Degraded
                       --> gh workflow run rollback.yml -f sha=<last-good>
                             --> revert commit --> Argo reconciles back
```

| Role | Who |
|---|---|
| Build and publish the image | GitHub Actions |
| Update desired state | GitHub Actions, as a commit |
| Reconcile desired state | Argo CD, inside the prod cluster |
| Verify the running result | Argo CD PostSync Job, inside the prod cluster |
| Roll back | `rollback.yml`, human-triggered |

## Repository layout

```
.github/workflows/
  ci.yml                            NEW   lint / test / build+push / promote
  rollback.yml                      NEW   workflow_dispatch
app/
  e2e/e2e_check.py                  NEW   the one verification script
  Dockerfile.e2e                    NEW   FROM the app image + COPY
deploy/
  helm/pokeproxy/
    values.yaml                     MOD   + e2e block, + image.digest support
    values-local.yaml               MOD   dev desired state (role unchanged)
    values-prod.yaml                REWRITTEN  GitOps desired state; the only file CI writes
    templates/_helpers.tpl          MOD   image-reference helper -> repo:tag@digest
    templates/e2e/job.yaml          NEW   dual-annotated hook Job
    templates/networkpolicy.yaml    MOD   +3 e2e rules
  argocd/
    install-values.yaml             NEW   pinned Argo CD config, 30s reconciliation
    application.yaml                NEW   the Application CR
  k3d/cluster.yaml                        dev, 8080 (unchanged)
  k3d/cluster-prod.yaml             NEW   prod stand-in, 8081
  k8s/namespace.yaml                      unchanged
  README.md                         MOD   GitOps section + external-prod delta
scripts/
  deploy.sh                         MOD   dev path: build e2e image, --short=7, full-sha label
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
| `main` branch protection | none — `gh api .../protection` returns `404 Branch not protected` | the promote job can push directly; no bypass token |
| Docker server arch | `amd64` | build `linux/amd64` only |
| `app/.python-version` | `3.13` | uv resolves it from the file; no version input in the workflow |
| `git rev-parse --short HEAD` | **7 characters** | matches `${GITHUB_SHA:0:7}`; CI and `deploy.sh` will agree |
| Is the downstream forward synchronous? | **yes** — `proxy.py:123` awaits `_forward_with_retry` before responding | when the E2E receives its 200, the mock has already recorded the payload. No polling race |
| `ruff format --check` | would reformat **13 of 27** files | D8 |
| Repo visibility | PUBLIC | GHCR packages can be public; anonymous pull |
| Local `gh` token scopes | `repo, read:org, gist, admin:public_key` — no `write:packages` | irrelevant: Actions publishes, the cluster only pulls |

## Order of work

| # | Step | Size | State |
|---|---|---|---|
| 1 | CI lint + test (`.github/workflows/ci.yml`), planning doc, WORKLOG | S | Not started |
| 2 | CI build + push to GHCR, short-sha tags, digests captured | S–M | Not started |
| 3 | **E2E: script, derived image, hook Job, 3 NetworkPolicy rules** — proven on the existing dev cluster | **L** | Not started |
| 4 | Prod stand-in cluster + Argo CD + rewritten `values-prod.yaml` (closes S4, N7) | M–L | Not started |
| 5 | CI promote job; measure commit to serving | S–M | Not started |
| 6 | `rollback.yml` plus all three failure scenarios executed | M | Not started |
| 7 | Issue write-ups, `deploy/README.md`, WORKLOG, AI_WORKFLOW | M | Not started |

Step 3 sits before step 4 deliberately: the E2E is the highest-value and highest-risk piece, it is fully provable on the cluster that already exists, and it stands on its own even if the prod cluster never happens.

## Step detail

### Step 1 — CI lint + test

Triggers `pull_request`, `push: [main]`, `workflow_dispatch`; `concurrency` with `cancel-in-progress`; `permissions: contents: read`. Two parallel jobs on `ubuntu-latest`: `ruff check .` and `pytest -q`, both with `working-directory: app`. `astral-sh/setup-uv` pinned to **0.12.5** to match the Dockerfile, `enable-cache: true`, `cache-dependency-glob: app/uv.lock`. Actions pinned by commit sha.

Chart linting is deliberately deferred to step 3, which is where the first chart change lands — wiring it now means rewriting it twice.

**Verification.** Draft PR from the feature branch; `gh run watch`, `gh run view --log`. Expect `All checks passed!` and `106 passed`. Second run to show a warm uv cache. Then a scratch commit that breaks one test, to prove the job actually goes red, dropped afterwards. Run URLs recorded.

### Step 2 — CI build + push to GHCR

`needs: [lint, test]`, `permissions: {contents: read, packages: write}`. Login with `GITHUB_TOKEN`; buildx; two images from `app/`; `platforms: linux/amd64`. Tag `ghcr.io/idanbro/<image>:${GITHUB_SHA:0:7}`. No `latest`, no moving tags. `type=gha` cache with a distinct scope per image. `build-args: GIT_SHA=${{ github.sha }}` — the **full** sha, because `org.opencontainers.image.revision` is conventionally full; `deploy.sh` changes to match so dev and CI images carry identical labels. Digests captured as job outputs for step 5.

**One manual action, mine to request and the operator's to perform:** after the first successful push, flip both GHCR packages to public in the GitHub UI. Packages are created private even from a public repo, and the prod cluster will `ImagePullBackOff` otherwise.

**Verification.** `docker pull ghcr.io/idanbro/pokeproxy@sha256:<digest>` anonymously from WSL; `docker inspect` to confirm the revision label equals the commit; second run compared for a warm buildx cache.

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

**Job template.** Gated on `.Values.e2e.enabled`. Carries **both** hook dialects — `helm.sh/hook: post-install,post-upgrade` and `argocd.argoproj.io/hook: PostSync`, each with its delete policy — so both paths are deterministic instead of relying on Argo CD's Helm-hook translation, which I intend to verify live in step 4 rather than trust. `backoffLimit: 0` with retries inside the script, so all output lands in one pod's logs. `activeDeadlineSeconds: 120` so a hang cannot wedge a sync. Full PSA-restricted securityContext, `enableServiceLinks: false`, `restartPolicy: Never`, `envFrom` the `pokeproxy-hmac` Secret, label `app.kubernetes.io/component: e2e`.

**NetworkPolicy — exactly three additions.** pokeproxy needs no new rule, because E2E traffic arrives *from Traefik*, which `allow-ingress-to-pokeproxy` already permits.

| Rule | Direction |
|---|---|
| `allow-e2e-egress-to-ingress` | e2e to kube-system :80 |
| `allow-e2e-egress-to-mock-downstream` | e2e to mock :8001 |
| `allow-mock-downstream-ingress-from-e2e` | mock from e2e :8001 |

**Verification, all on the dev cluster, before any prod cluster exists.**

| Check | Expected |
|---|---|
| `helm lint --strict`, `helm template` | Job renders only when `e2e.enabled` |
| `deploy.sh` with e2e on | hook Job runs, four assertions pass, logs captured |
| run twice back to back | passes both times — proves the dedup handling is real |
| point the e2e at a wrong mock URL | Job fails, `helm upgrade --atomic` auto-rolls-back; `helm history` captured |
| delete one of the three NetworkPolicy rules | Job fails on connection timeout — the same A/B rigour as Part 2 step 8; proves the rules are load-bearing |
| `/received` length | grows by exactly 1 per passing run |

`helm lint --strict` and `helm template | kubeconform -strict` join CI in this step.

### Step 4 — Prod cluster and Argo CD

`bootstrap-prod.sh`, idempotent, and the order matters: create cluster if absent, apply `namespace.yaml` with its PSA labels, generate and apply `.secrets/sealing-key-prod.yaml` **before** the controller, install sealed-secrets (same pinned version, `keyrenewperiod=0`), install Argo CD from `install-values.yaml`, apply the Application, print the admin password and a port-forward hint. `KUBE_CONTEXT` pinned on every cluster-touching call: the S5 lesson, applied from the start this time.

`seal-hmac.sh` gains `--env {local,prod}` and the **N7 fix**. This stops being cosmetic here — the target file will hold image tags and digests, so a wholesale `cat >` becomes data loss. Targeted key replacement via yq.

The Application: `repoURL https://github.com/IdanBro/PokeProxy`, `targetRevision main`, `path deploy/helm/pokeproxy`, `helm.valueFiles: [values-prod.yaml]` (in-chart, per D9), `syncPolicy.automated` with `prune` and `selfHeal`, retry backoff, and the namespace deliberately not owned by Argo.

Rewritten `values-prod.yaml`: GHCR repositories, tag and digest seeded once by hand from step 2 then owned by CI, Traefik ingress, `mock-downstream.enabled: true` (the E2E's downstream assertion needs a sink; an external production would need a synthetic sink instead — documented as the delta), `e2e.enabled: true`, HMAC sealed against the prod cluster's own key.

**Verification.**

| Check | Expected |
|---|---|
| `bootstrap-prod.sh` from scratch | cluster, sealed-secrets, Argo CD and Application, one command |
| Argo CD status | Synced / Healthy, 4/4 pods |
| `kubectl describe pod` | image reference is `ghcr.io/...@sha256:...` — pulled from the registry, not imported |
| PostSync Job | ran and passed — this is where the hook-annotation translation gets proven |
| both clusters | dev still serving on 8080, prod on 8081, contexts distinct |
| signed request to `localhost:8081/stream` | 200 |
| hand-edit a live Deployment | selfHeal reverts it — proves continuous reconciliation, not just first apply |

### Step 5 — CI promote

`needs: [build]`, `if: github.ref == 'refs/heads/main'`, `permissions: contents: write`, `concurrency: promote` so two merges cannot race. yq writes six fields — tag and digest for pokeproxy, mock-downstream and e2e — into `values-prod.yaml`. Commits as `github-actions[bot]`, subject `chore(deploy): promote <sha>`, digests in the body, `git pull --rebase` then push.

**Verification.** Merge a trivial app change and follow the whole chain. Confirm the promote commit did **not** trigger a second CI run. Confirm the digest in git equals `kubectl get pod -o jsonpath=...` on the prod cluster. Measure and record **commit to serving** in seconds; that number belongs in the README as a measurement, not a claim.

### Step 6 — Rollback

`rollback.yml`, `workflow_dispatch` with input `sha`. Resolves that sha's digests from GHCR via `docker buildx imagetools inspect`, so the operator supplies only a sha. Writes tag and digest, commits `revert(deploy): roll back to <sha>`, pushes. Argo reconciles within the poll interval.

Three scenarios, executed rather than described:

| Scenario | Setup | Expected |
|---|---|---|
| A — rollout failure | promote an image that crashes on boot | new pods never Ready, old pods keep serving, Argo Degraded, **zero failed requests** measured with the load generator against 8081 throughout |
| B — verification failure | promote an image that boots healthy but forwards wrongly | pods Ready, PostSync E2E fails, sync Failed; **measure the exposure window**; roll back and confirm recovery |
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
| Healthy pods, functionally wrong (bad rules, broken HMAC, downstream never called) | **Yes**, for the duration of the E2E | exactly the class the PostSync E2E exists to catch — seconds, not minutes |
| Unrenderable chart or invalid manifest | **No** | `helm lint --strict` and `kubeconform` in CI |

So the uncovered class is precisely one — healthy-but-wrong, exposed for the length of one E2E run. That is the honest cost of deferring the ephemeral pre-promotion cluster, and it is a cost I can state and defend rather than one I have to discover.

A PostSync hook **detects**; it does not **prevent**. Describing it as a gate would be describing detection as prevention.

### Rollback

| Failure | Detection | Response | Automated? |
|---|---|---|---|
| Rollout failure | probes; Argo health goes Degraded | old ReplicaSet keeps serving; nothing to do | yes, structurally |
| PostSync E2E fails | Argo sync Failed, app Degraded | `gh workflow run rollback.yml -f sha=<last-good>` | detection automatic; response one command |
| Bad version found later | humans, or Part 4 alerts | same path | same |

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
- **New backlog opened by this Part:** mock-downstream's `received_pokemon` is an in-process list that grows forever, and every E2E run appends to it on a long-lived prod cluster. The prod sealing key is gitignored, so a fresh clone cannot reconstitute the prod cluster's secret — the same accepted trade-off already documented for dev. Argo CD admin credentials need a handling decision.

## Definition of done for Part 3

- CI green on real GitHub Actions across lint, test, build+push and promote, with run URLs recorded. Not yet.
- Three images in GHCR at short-sha tags, pullable anonymously by digest. Not yet.
- Prod cluster reconciled by Argo CD from `main`, pods verifiably running GHCR digests. Not yet.
- PostSync E2E sends real protobuf and HMAC through the ingress and validates the mock downstream result, passing, with logs captured. Not yet.
- Commit to serving time measured and recorded. Not yet.
- All three rollback scenarios executed with captured output. Not yet.
- S4 and N7 closed with write-ups. Not yet.
- Anything not verified by execution is labelled as not verified.
