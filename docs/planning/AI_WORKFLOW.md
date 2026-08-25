# AI-Assisted Engineering Workflow

I used Claude Code as a pair-engineering and review tool during this assignment. I intentionally kept implementation decisions incremental so that I could review and understand each change rather than asking the model to solve the assignment end-to-end.

## Operating rule — token economy

Maximum information in minimum cost. This governs every response, document and tool call.

- **Output:** lead with the answer. Tables and lists over prose. No preamble, no restating the question, no summarizing what was just said. Length has to be earned by information, not by thoroughness theater.
- **Tool calls:** batch independent calls, read the specific lines needed rather than whole files, don't re-verify what is already established.
- **Never compressed:** evidence, exact numbers, `file:line` references, honest uncertainty, and the reasoning behind a decision I would otherwise have to ask about.

Compress the packaging, never the substance. Terse is the goal; vague is a failure. The rule lives in `CLAUDE.md` so every session inherits it without being re-told.

## Session model

I used a fresh Claude Code session for each major workstream:

1. Repository review / assignment decomposition
2. Part 1 — production hardening
3. Part 2 — containerization and Kubernetes
4. Part 3 — CI/CD and GitOps
5. Part 4 — observability
6. Part 5 — bootstrap and end-to-end verification
7. Final adversarial review

If a workstream accumulated too much debugging history, I started a clean continuation session after first updating `WORKLOG.md`.

`CLAUDE.md` defines the stable collaboration rules. `WORKLOG.md` contains the current project state, decisions, backlog, and handoff context between sessions.

## Change workflow

For meaningful changes I used the same loop:

1. Claude investigates and explains the problem without editing code.
2. We discuss alternatives and tradeoffs.
3. I approve a specific implementation scope.
4. Claude implements only that scope and runs relevant verification.
5. The resulting diff is reviewed skeptically.
6. The decision and verification are documented.
7. Only then do we move to the next issue.

## Prompt Log

The prompts below are intentionally concise. Repository state and decisions live in the repository instead of being repeated in every conversation.

### Session 01 — Repository Review

```text
We are starting with a read-only review of the Guardio PokeProxy assignment.

Read CLAUDE.md, README_HOME_ASSIGNMENT.md, WORKLOG.md, and the complete repository. Inspect git status as well.

Do not modify any files yet.

I want you to:
1. Explain the application architecture and end-to-end request flow in plain language.
2. Review Part 1 from a production/operations perspective.
3. Build a prioritized issue inventory: Critical / High / Medium / Low.
4. For each finding, give: evidence in the code, realistic production impact, and likely direction — but do not implement anything.
5. Identify decisions in Part 1 that could constrain Docker, Kubernetes, CI/CD, GitOps, observability, E2E, or bootstrap later.
6. Recommend the order in which we should tackle Part 1.
7. Pick the single first issue you recommend addressing and explain it using the pre-change workflow from CLAUDE.md.

Do not write code. Stop after the first issue explanation and wait for me.
```

### Session 01 — Persist the agreed review

```text
I agree with the review direction.

Do not modify application code.

Update WORKLOG.md with the initial assessment, prioritized Part 1 backlog, current state, and any items that belong under Backlog / Later.

Create docs/planning/part-01-production-hardening.md containing only the planning we actually discussed: approach, priorities, important alternatives, and why we chose the order of work.

Keep both documents concise and written as engineering notes from my perspective.

Then show me the documentation diff and stop.
```

### Session 02 — Part 1: Production Hardening

```text
We are starting the implementation phase of Part 1 — Code Review & Production Hardening.

Read CLAUDE.md, README_HOME_ASSIGNMENT.md, WORKLOG.md, docs/planning/part-01-production-hardening.md, and inspect the current git status/diff.

Do not modify anything yet.

Reconcile the current repository state with the Part 1 backlog. If something changed since the review, call it out.

Then select the highest-priority unresolved issue and explain ONLY that issue using the pre-change workflow from CLAUDE.md.

Do not implement it yet. Stop for my approval.
```

### Reusable — Approve one implementation

```text
Approved.

Implement only the issue we just agreed on.

Please write a clean code, no comments, understandable, using SOLID principles. the code should explain itself instead of having comments doing it.

Add or update regression tests where appropriate, run the relevant checks, inspect the final git diff, update WORKLOG.md, and create/update the corresponding docs/issues/ write-up.

Remove any code/tests that are not correlating with the current and new implementation, in order to maintain a small and relevant codebase.

Do not start another issue.

At the end, summarize:
- what changed
- what verification actually ran
- any remaining risk or follow-up

Then stop.
```

### Reusable — Skeptical diff review

```text
Before we move on, act as a skeptical Senior/Staff DevOps reviewer.

Review ONLY the diff we just created and the behavior it changes.

Look for:
- correctness problems
- realistic production failure modes
- hidden behavior changes
- unnecessary complexity
- missing tests
- security/operability regressions
- assumptions that are not documented

Do not modify anything.

Rank findings as BLOCKER / SHOULD FIX / NICE TO HAVE. If there is nothing meaningful, say so explicitly.
```

### Reusable — Commit changes

```text
Look at the current git status.

Commit all changes.

Commit message is max 2 sentences. 

Authour is only me. 

Push to current branch.
```

### Reusable — Next issue

```text
Continue Part 1.

Read the current WORKLOG.md and git state, then pick the next highest-priority unresolved Part 1 issue.

Explain simply and understandable from high level only that issue using our pre-change workflow.

Do not modify anything until I approve.
```

### Part 1 completion gate

```text
Before we declare Part 1 complete, reread the Part 1 requirements in README_HOME_ASSIGNMENT.md and perform a requirement-by-requirement audit against the current repository.

Do not start Part 2.

Check specifically that reliability issues, configuration/secrets hygiene, structured logging, graceful shutdown, useful errors, operability improvements, tests, and per-issue documentation are actually covered.

Run the relevant Part 1 verification that is safe to run locally.

Then give me:
1. requirements satisfied
2. remaining gaps
3. BLOCKER / SHOULD FIX / NICE TO HAVE findings
4. anything deliberately deferred and why

Update WORKLOG.md and docs/planning/part-01-production-hardening.md only with factual final state, then stop.
```

### Session 03 — Part 2: Infrastructure & Deployment

```text
We are starting Part 2 — Infrastructure & Deployment in a fresh session.

Read CLAUDE.md, README_HOME_ASSIGNMENT.md, WORKLOG.md, the Part 1 planning/issues that matter, and inspect git status/diff.

Do not implement anything yet.

Design the Part 2 approach and explicitly evaluate:
- production Docker image design
- local Kubernetes choice (k3d / kind / minikube or another justified option)
- raw manifests vs Kustomize vs Helm
- application, Redis, and mock-service topology/networking
- ConfigMaps and Secrets
- health probes and graceful termination
- resource requests/limits and container security
- local image workflow and future CI registry workflow
- what must remain compatible with later GitOps, monitoring, E2E, and one-command bootstrap

Prefer the simplest production-reasonable design I can clearly defend in an interview.

Give me the options, recommendation, tradeoffs, proposed repository layout, and ordered implementation steps.

Do not create files yet. Pick only the first implementation step and stop for my approval.
```

### Part 2 completion gate

```text
Audit Part 2 against README_HOME_ASSIGNMENT.md before we move on.

Verify the real deployed behavior, not just manifest syntax: containers build, the cluster resources become healthy, services can reach each other using cluster networking, configuration/secrets are correct, and probes/resources/security settings behave as intended.

Do not start CI/CD.

Report remaining gaps by BLOCKER / SHOULD FIX / NICE TO HAVE, update WORKLOG.md and the Part 2 planning document with factual state, then stop.
```

### Session 04 — Part 3: CI/CD & GitOps

```text
We are starting Part 3 — CI/CD & GitOps in a fresh session.

Read CLAUDE.md, README_HOME_ASSIGNMENT.md, WORKLOG.md, the current deployment configuration, and inspect git status/diff.

Do not implement anything yet.

Design the delivery flow from commit to verified deployment.

Evaluate and explain:
- CI stages for lint/test/build
- image registry and immutable image versioning
- caching/reproducibility
- direct scripted deployment vs GitOps
- if GitOps: who builds, who updates desired state, who reconciles it, and how image versions are represented
- post-deploy E2E that sends real protobuf + HMAC traffic through PokeProxy and validates the mock downstream result
- how verification gates deployment
- rollback for rollout failure, verification failure, and a bad version discovered later
- what can realistically run locally vs what is defined/demonstrated

Do not call a process GitOps if CI is directly mutating the cluster behind Git's back.

Recommend the simplest coherent architecture and repository layout, including tradeoffs.

Then pick only the first implementation step and stop for my approval.
```

### Part 3 completion gate

```text
Audit Part 3 requirement-by-requirement against README_HOME_ASSIGNMENT.md.

Trace one hypothetical commit all the way through CI, image publication/versioning, desired-state change or deployment, rollout, post-deploy E2E, failure handling, and rollback.

Identify any stage that is hand-wavy, non-runnable, or inconsistent with the documented architecture.

Do not start observability.

Report BLOCKER / SHOULD FIX / NICE TO HAVE findings, update WORKLOG.md and the Part 3 planning document with factual state, then stop.
```

### Session 05 — Part 4: Observability

```text
We are starting Part 4 — Observability in a fresh session.

Read CLAUDE.md, README_HOME_ASSIGNMENT.md, WORKLOG.md, the hardened application, and current Kubernetes deployment.

Do not implement anything yet.

Design an observability approach that answers the assignment's question: "Is this service healthy right now?"

Evaluate:
- application RED-style metrics where appropriate
- downstream/cache/routing signals that are operationally useful
- metric naming and label cardinality
- process/runtime/Kubernetes resource metrics vs custom application metrics
- Prometheus-compatible instrumentation
- monitoring stack deployment
- one focused Grafana dashboard
- at least one meaningful alert with justified threshold/duration
- one signal we intentionally choose NOT to alert on and why

Avoid vanity metrics and high-cardinality labels.

Give me the proposed metrics, dashboard panels, alerts, tradeoffs, and ordered implementation steps.

Do not change files yet. Pick only the first step and stop for my approval.
```

### Part 4 completion gate

```text
Audit Part 4 against README_HOME_ASSIGNMENT.md.

Verify that metrics are actually scraped, dashboard queries resolve to real data, and alert expressions are valid and capable of firing under the documented condition.

Check whether the dashboard can answer "Is PokeProxy healthy right now?" without requiring someone to inspect twenty unrelated charts.

Do not start Part 5.

Report BLOCKER / SHOULD FIX / NICE TO HAVE findings, update WORKLOG.md and the Part 4 planning document, then stop.
```

### Session 06 — Part 5: Zero to Running + E2E

```text
We are starting Part 5 — Automation: Zero to Running in One Command in a fresh session.

Read CLAUDE.md, README_HOME_ASSIGNMENT.md, WORKLOG.md, and the current Docker/Kubernetes/monitoring/E2E tooling.

Do not implement anything yet.

Design the smallest clear automation that takes a clean supported machine to the full running stack with one entry point and also provides teardown.

Evaluate:
- prerequisite validation
- cluster creation/reuse
- image build/import or pull flow
- application/Redis/mock deployment
- monitoring installation
- waiting for actual readiness
- post-deploy functional verification
- idempotency when run twice
- failure messages and cleanup behavior
- teardown
- whether Makefile, shell scripts, or a small CLI is the clearest orchestration layer

Avoid hiding large fragile shell programs inside Makefile recipes.

Give me the proposed command UX, sequence, failure model, repository layout, and tradeoffs.

Do not implement yet. Pick only the first step and stop for my approval.
```

### Part 5 completion gate

```text
Perform a clean-machine-style audit of Part 5.

Starting only from the documented prerequisites, trace the exact one-command bootstrap path and teardown path.

Verify idempotency by reasoning about and, where feasible, actually rerunning the entry point.

Check that success means the full monitored application is running and functional, not merely that Kubernetes objects were applied.

Report BLOCKER / SHOULD FIX / NICE TO HAVE findings and update WORKLOG.md and the Part 5 planning document with the final factual state.

Do not begin bonus work.
```

### Session 07 — Final Adversarial Review

```text
The five required Parts are now intended to be complete.

Act as a Guardio Senior/Staff DevOps interviewer grading this submission.

Read the entire assignment, repository, README, WORKLOG, planning artifacts, issue documentation, CI/CD, Kubernetes, monitoring, automation, tests, and relevant git history/diffs.

Do not modify anything.

Perform an adversarial review across:
- production correctness and failure modes
- code hardening
- security/config/secrets
- Docker
- Kubernetes
- CI/CD and actual GitOps semantics
- post-deploy E2E
- rollback
- observability/dashboard/alerts
- one-command bootstrap and teardown
- idempotency/reproducibility
- documentation quality
- AI/planning artifacts
- unnecessary complexity
- requirements that are technically present but not truly demonstrated

Map every assignment deliverable to the exact files that satisfy it.

Rank all findings:
- BLOCKER
- SHOULD FIX
- NICE TO HAVE

Then give me the 10 technical interview questions you would be most likely to ask based specifically on choices in this repository.

Stop. Do not fix anything until I choose a finding.
```

### Final submission gate

```text
Do one final requirement-by-requirement submission audit against README_HOME_ASSIGNMENT.md.

Do not make changes.

For every deliverable, show:
- where it is implemented/documented
- how it was verified
- any limitation I should disclose

Then give me:
1. a final BLOCKER list
2. a final SHOULD FIX list
3. commands I should run once myself before submitting
4. files I should make sure are included in the ZIP/repository
5. anything that should NOT be included (temporary files, local secrets, generated junk)

If there are no blockers, say that explicitly.
```

### Continuation after a context reset

```text
This is a continuation session for the current workstream.

Reconstruct context from the repository instead of guessing.

Read CLAUDE.md, README_HOME_ASSIGNMENT.md, WORKLOG.md, the relevant docs/planning/ file, relevant docs/issues/ files, and inspect git status/diff.

Tell me:
1. what is already complete
2. what is currently in progress
3. any uncommitted changes
4. the next single decision/change according to the current plan

Do not modify anything yet. Stop after reconstructing the state.
```

---

## Session Notes

Factual conversation-flow notes, appended per workstream. What the session focused on, corrections I made, and which decisions resulted. Not transcripts.

### Session 03 — Part 2 design + step 1 (2026-08-23)

**Focus:** design the whole of Part 2 before writing anything, then implement only the first step.

**Corrections I made to the proposed design — three, all deliberate:**

1. **Kustomize → Helm.** Claude recommended Kustomize for our workloads and Helm only for upstream charts, arguing `configMapGenerator`'s content-hash naming solves the H1 rules-restart problem for free and that `kustomize edit set image` is the natural GitOps primitive. I overruled it: I don't want two packaging approaches across local and production. Claude accepted, mapped every mechanism to its Helm equivalent (`checksum/config` annotation, values-file image bump), and pointed out that `helm upgrade --atomic` plus `helm rollback` revision history is a real gain back for Part 3's rollback story.

2. **CPU limits.** Claude proposed requests-only with no CPU limits, on the grounds that CFS throttling costs tail latency on a proxy and that would poison Part 4's alert thresholds. I asked for limits at 2× requests. Accepted, with the caveat that the *requests* have to come from `kubectl top` measurement in step 9 rather than a guess — the provisional 250m for pokeproxy was raised from an earlier 100m specifically because a 200m ceiling would throttle under load.

3. **Redis via a Helm chart.** I asked for the Bitnami chart and invited pushback. Claude pushed back with evidence: the August 2025 Bitnami catalog change moved versioned images to `docker.io/bitnamilegacy/*` (archived, unpatched) and stopped OCI chart publishing, and even `architecture: standalone` brings a StatefulSet, PVC, auth Secret and sentinel/metrics templates we'd disable. It also noted a third-party subchart is itself a second templating approach, which cuts against my own consistency argument for #1. I took the recommendation: Redis is templated in our chart on the official image.

**A correction Claude made to me:** I proposed installing the kubeseal controller at cluster creation so decryption would be "the same process every time." That doesn't work on its own — the controller mints a fresh keypair whenever it finds no Secret labeled `sealedsecrets.bitnami.com/sealed-secrets-key: active`, so every recreated k3d cluster would break a committed SealedSecret. The fix is to pin the sealing key: generate it into a gitignored `.secrets/`, apply it *before* the controller starts, install with `keyrenewperiod=0`.

**Also settled:** WSL bash as the single control shell (Part 5's bootstrap must run on a Linux CI runner); k3d over kind/minikube/Docker Desktop (Docker Desktop's cluster lifecycle is a GUI toggle and fails Part 5 outright); a `__main__.py` config preflight in step 2 to close R4 and M6; single Helm chart with `mockDownstream.enabled: false` in prod values.

**Result:** approved plan at `docs/planning/part-02-infrastructure-deployment.md`, 10 ordered steps. Step 1 (Docker image) implemented and verified by execution — measurements in `WORKLOG.md`, not asserted.

### Session 04 — Part 2 completion audit (2026-08-23)

**Focus:** audit Part 2 against `README_HOME_ASSIGNMENT.md` before starting Part 3. My explicit framing, and the thing that shaped the whole session: *verify the real deployed behavior, not just manifest syntax* — containers build, resources become healthy, services reach each other over cluster networking, config/secrets are correct, probes/resources/security behave as intended. Also explicit: do not start CI/CD, and stop after reporting.

**The instruction changed what the audit actually did.** Reading templates would have produced a plausible, mostly-wrong report. Three of the findings only exist because something was executed:

- The cluster was running images from `95b5887` while HEAD was `721b8fc`, so the first action was rebuilding both images at the HEAD sha and redeploying — otherwise every result below would have described a tree that isn't the one being submitted.
- **B1** (the sealed HMAC ciphertext only decrypts on this machine) was proven by sealing a value with a foreign 4096-bit key, applying it, and reading the controller's `no key could decrypt secret` back — not by reasoning about `seal-hmac.sh`. The same test showed the controller's active key fingerprint is byte-identical to the gitignored `.secrets/sealing-key.yaml`, which is what makes the conclusion airtight.
- **S2** (`enableServiceLinks`) was found by dumping the environment inside a running pod and noticing Kubernetes had injected `POKEPROXY_PORT=tcp://10.43.93.39:8000` into every pod in the namespace. pokeproxy works today only because container `envFrom` outranks service links. No amount of chart reading surfaces that.

**What the audit confirmed rather than corrected.** Most of Part 2 held up under execution: ingress-only `/stream` exposure, the 413 body cap, PSA rejecting a privileged pod, NetworkPolicy refusing an unlabeled pod, and Redis-scaled-to-zero leaving pods `Ready` with zero 5xx. The rolling-restart claim was re-run with **all-unique payloads** rather than the load generator's 12 fixed ones, so the 1113/1113 × 200 result exercises the real forward path instead of dedup replays — a strictly stronger version of step 9's measurement.

**One documentation correction:** `WORKLOG.md` recorded 101 tests; the suite is at 106 (the step-2 entrypoint tests were never folded into the count).

**Result:** 2 BLOCKERs (B1, B2), 4 SHOULD FIX (S1–S4), 6 NICE TO HAVE (N1–N6). Both blockers are about reproducing the deployment outside this machine, neither affects the running cluster, and both are prerequisites for Part 3's CD and Part 5's bootstrap. Full evidence in `docs/planning/part-02-infrastructure-deployment.md`; tracked in `WORKLOG.md`'s backlog. No code or chart changed by the audit itself.

### Session 05 — B1/B2 blocker fixes (2026-08-23, same day as the Part 2 audit)

**Focus:** fix the two BLOCKERs from the Part 2 audit before starting Part 3.

**A correction I made to my own recommendation, before writing any code.** When asked which B1 fix I'd recommend, I first said "commit the public cert, seal offline with `kubeseal --cert`." Working through the implementation, I realized that doesn't actually solve the problem on its own: the cert only decrypts things sealed for its matching private key, and that private key is randomly regenerated on every fresh clone by `generate_sealing_key()`. Committing the cert without also pinning the private key just moves the same mismatch one step later. I flagged this to the user rather than silently implementing something that wouldn't fix the bug, and implemented the smaller alternative instead: re-seal unconditionally whenever the script just minted a new key. No key material goes into git either way.

**Verification method for both fixes: reproduce the exact failure, then prove it's gone.** For B1, that meant deleting the local sealing key file and re-running the script against the live cluster — not just reading the new code and reasoning about it. It generated a new key, printed the new re-seal message, produced different ciphertext, and a redeploy against that ciphertext succeeded with the decrypted secret matching the expected dev key byte-for-byte. Also checked idempotency didn't break: a second run with the key unchanged left the file untouched. For B2, applied the new `deploy/k8s/namespace.yaml` against the already-existing namespace (no-op, labels unchanged) and separately against a namespace that had never existed (created correctly with full PSA enforcement) — proving the file works both as a no-op on the current cluster and as the real bootstrap step on a fresh one.

**Cleanup:** both experiments touched live cluster state (a different sealing key, a scratch namespace) — the sealing key and `values-local.yaml` were restored to their original values and the cluster redeployed clean before finishing, so the audit's revision-8 state isn't left divergent by the verification process itself.

**Result:** `scripts/seal-hmac.sh` fixed (3-line change); new `deploy/k8s/namespace.yaml`; write-ups `docs/issues/017-sealed-secret-key-portability.md` and `docs/issues/018-namespace-not-tracked.md`; `WORKLOG.md` and the Part 2 planning doc updated to reflect both as fixed. S1–S4 (should fix) and N1–N6 (nice to have) remain open, unaffected by this session.

### Session 06 — S1/S2/S3 fixes (2026-08-23, same day as the audit and B1/B2 fixes)

**Focus:** fix the three remaining SHOULD FIX chart-hygiene items from the Part 2 audit.

**S1 decision:** the write-up left two options open — gate the templates, or delete the unused `enabled` keys. Deleting them was tempting (pokeproxy and redis aren't really meant to be toggled off), but the `enabled` field is read generically by `serviceaccount.yaml`'s `range` loop over every component — deleting the key would silently break ServiceAccount creation via Helm's nil-is-falsy behavior. Gating the Deployment/Service templates, matching the pattern already fixed for mock-downstream in `721b8fc`, was the correct minimal fix: same bug class, same shape of fix, no ripple into unrelated templates.

**S2 was applied to all three components, not just the one that broke.** The audit only proved the failure on mock-downstream (it has no `envFrom` to mask the injected var), but pokeproxy and redis have the identical exposure — pokeproxy is saved today only by env-precedence luck. Fixed all three rather than just the one caught failing.

**Verification, same standard as the B1/B2 session: reproduce, fix, re-verify live, not just re-read the template.** For S1, rendered the chart with `--set components.redis.enabled=false` before the fix (still produced an orphaned Deployment) and after (zero Redis resources at all). For S2, redeployed live and diffed the mock-downstream pod's environment before/after — `POKEPROXY_PORT=tcp://...` plus four `_TCP_*` variables gone entirely. Ran the full app suite (106 passed, ruff clean) and a signed end-to-end request through the real ingress afterward to confirm the chart changes didn't regress anything already working.

**S3's README was written command-by-command against the live cluster, not drafted from memory of what the steps "should" be.** Each step in `deploy/README.md` — cluster create, build+import at the exact HEAD sha, namespace apply, secret seal, `helm upgrade --atomic`, a verification curl — matches a command already re-run and confirmed working during this and the prior two sessions, including the sha-drift warning learned the hard way in Part 2 steps 6–8.

**Result:** `deploy/helm/pokeproxy/templates/pokeproxy/{deployment,service}.yaml`, `redis/{deployment,service}.yaml` gated on `enabled`; `enableServiceLinks: false` added to all three Deployments' pod specs; new `deploy/README.md`. `WORKLOG.md` and the Part 2 planning doc updated to mark S1–S3 fixed. S4 and N1–N6 remain open, untouched by this session. Stopped here per instruction, for review.

### Session 07 — scripts/deploy.sh (2026-08-23)

**Focus:** turn `deploy/README.md`'s manual steps into a single runnable, idempotent script, as asked.

**Running it caught a real bug in the README that reading it never would have.** The documented verify command, `curl -i http://localhost:8080/stream`, sends a bare GET — but `/stream` is POST-only, so it returns **405**, not the 401 the README claimed. That line had never actually been executed as written; it was transcribed from what the app-level behavior *should* look like rather than run against the ingress. The script's verify step (built to assert on the status code, not just print it) failed loudly on first run and surfaced this immediately. Fixed both the script and the README to use `-X POST`, then re-ran twice to confirm: two consecutive full runs against the live cluster, revisions 12 and 13, both `4/4 pods Ready, 0 restarts`, both a genuine `401`.

**Idempotency was designed in, not bolted on.** `k3d cluster list <name>` before creating (reuse if it exists), `kubectl apply` for the namespace (already idempotent), `seal-hmac.sh` unchanged (already fixed to be idempotent in the B1 session), `helm upgrade --install --atomic` (idempotent by construction). The second `deploy.sh` run confirmed all of these held: cluster reused, namespace `unchanged`, sealing key and ciphertext both left untouched, only the Helm revision incremented.

**Result:** new `scripts/deploy.sh`; one-line fix to `deploy/README.md`'s verify step. `WORKLOG.md` updated. No chart or app code touched.

### Session 08 — Part 2 re-audit at HEAD `cd72953` (2026-08-23)

**Focus:** audit Part 2 against `README_HOME_ASSIGNMENT.md` a second time, verifying deployed behavior rather than manifests. Explicit instruction: do not start CI/CD; report gaps by severity; update `WORKLOG.md` and the Part 2 planning doc; stop.

**The first thing the audit found was drift, again.** The cluster was running `pokeproxy:cbb7911` while HEAD was `cd72953` — session 07's two `deploy.sh` runs happened *before* the commit that added the script existed, so the images were tagged one commit behind. Same failure mode Part 2 hit in steps 6, 7 and 8. It costs nothing here because `deploy.sh` rebuilds at `git rev-parse --short HEAD`, but it is the fourth occurrence and it is exactly what Part 3's pipeline removes structurally.

**The decision that made this audit worth running: execute the from-zero path, not just re-check the reuse path.** `deploy.sh` had only ever been run against a cluster that already existed. The claim "a new engineer can stand this up" was still untested. So after running the reuse branch (2m08s, revision 14), I deleted the k3d cluster outright and ran the script once: **4m39s from zero clusters to 4/4 pods Ready and a verified ingress**, `Release "pokeproxy" does not exist. Installing it now.` That also closed the one caveat left on B1 — the committed ciphertext decrypted on a cluster that had never existed, because the sealing key is re-pinned into a freshly installed controller.

**I deliberately did not delete `.secrets/sealing-key.yaml`.** Simulating a true fresh clone would have regenerated the key and rewritten the tracked `values-local.yaml`, leaving the working tree dirty. That specific path was already proven live in the B1 session; the cluster-deletion test proves a different and previously untested property. Recorded the distinction rather than silently substituting one for the other. `values-local.yaml` sha256 was checked before and after both runs and never moved.

**One probe result was nearly a false positive, and catching it mattered.** The first NetworkPolicy test used Service DNS names and reported `DNS_FAIL` alongside three denials — which would have meant the denials were DNS failures, not policy hits, invalidating the whole check. Re-ran it printing `nslookup` output and connecting to raw ClusterIPs instead: DNS resolves correctly (`pokeproxy-redis.pokeproxy.svc.cluster.local` → `10.43.209.105`), busybox `nslookup` just exits non-zero on the search-domain NXDOMAINs it tries first. The three denials are genuine, and now proven without DNS in the path.

**Two new SHOULD FIX items, both found by asking "what does this script do on a machine that isn't this one?"** S5: neither `deploy.sh` nor `seal-hmac.sh` pins a kube context, and `k3d cluster list` is context-independent — measured exit 0 under both `k3d-pokeproxy` and `docker-desktop`. On the reuse path the script skips the only step that would have set the context, then applies a namespace, a private RSA key and a Helm release into whatever context is current. It fails loudly here only because Docker Desktop's Kubernetes is off. S6: S1's `enabled` fix covered the Deployment and Service but not the five other pokeproxy-owned templates, so `enabled=false` still renders an Ingress backing onto a Service that was never created.

**S4 turned out to be worse than it was written down as.** The recorded half is that `values-prod.yaml` renders rules pointing at the mock Service it disables. The unrecorded half: `allow-pokeproxy-egress-to-dependencies` only permits egress to in-namespace redis and mock pods, so even with correct URLs, `default-deny-all` blocks every packet to a real external downstream. Updated the write-up rather than leaving a half-true entry in the backlog.

**Result:** no code or chart changed. New "Part 2 re-audit at HEAD `cd72953`" section in `docs/planning/part-02-infrastructure-deployment.md` with the full evidence table; `WORKLOG.md` Current State and backlog table updated with S5, S6, N7, N8 and the sharpened S4. Stopped here per instruction, for review.

### Session 09 — fixing S5 and S6 (2026-08-23)

**Focus:** implement the two SHOULD FIX items the session-08 re-audit found. Instruction was exactly "fix S5 and S6" — S4 explicitly not in scope, and left open.

**S5's fix shape was the interesting decision, not the bug.** Three ways to pin a context: `kubectl config use-context` at the top, hardcode `--context k3d-pokeproxy` everywhere, or derive it from the cluster name into an overridable variable and pass it explicitly. Went with the third. The argument against `use-context` is that it fixes the symptom by *taking over* the operator's shell instead of making the script independent of it — a deploy script that repoints your `kubectl` as a side effect is its own small footgun. Verified the distinction held: after a successful run the current context was still `docker-desktop`, untouched.

**Proved the fix by reproducing the original misfire first.** Ran the old bare `kubectl apply -f deploy/k8s/namespace.yaml` under the `docker-desktop` context and watched it try to reach `kubernetes.docker.internal:6443`. Then ran the fixed `deploy.sh` from that same wrong context and watched it deploy to `k3d-pokeproxy` anyway. Reproduce-then-fix-then-re-verify, the same standard as the B1/B2 and S1–S3 sessions.

**S6 nearly shipped a silent regression, and the render diff is what caught it.** The obvious placement — `{{- if … }}` after the existing `{{- $var := … -}}` assignments in `configmap-env.yaml` — leaves the `if`'s closing `}}` emitting a newline before `apiVersion:`, because nothing following it starts with `{{-` to strip it. That changed the ConfigMap's rendered bytes, which changed `include … | sha256sum`, which changed `checksum/config-env`, which would have rolled every pokeproxy pod on the next upgrade for a pure whitespace edit. Only visible by diffing full renders against `git show HEAD:` copies of the five templates in a scratch chart. Moving the `if` above the assignments made both `values-local` and `values-prod` renders byte-identical, and the live redeploy confirmed it — checksums unchanged, no pod rolled.

**Deliberately did not gate the four pokeproxy NetworkPolicies**, even though the audit listed them. Checked each one rather than assuming: two select a pod that no longer exists, and the other two permit ingress *from* a selector matching nothing, which leaves redis and mock exactly as closed as `default-deny-all` already makes them. All four are inert, and gating them would thread a per-component flag into a file switched by `networkPolicy.enabled`. Recorded the reasoning in the write-up so the omission reads as a decision, not an oversight.

**Fixed N8 in passing** since the README was being edited anyway: it claimed re-sealing happens on a "fresh clone or fresh cluster", but session 08's from-zero run proved a fresh cluster with the key still on disk correctly does *not* re-seal.

**Result:** `scripts/{deploy,seal-hmac}.sh` context-pinned; five chart templates gated; `deploy/README.md` updated to match and N8 corrected; write-ups `docs/issues/019-deploy-scripts-unpinned-kube-context.md` and `020-pokeproxy-enabled-flag-incomplete.md`. Joint verification: E2E through the real ingress 11/11, `pytest -q` 106 passed, `ruff` clean. S4 and N1–N7 remain open, untouched. Stopped here for review.

### Session 10 — Part 3 design (2026-08-23)

**Focus:** design the whole delivery flow before writing anything. Explicit instruction, and the one that shaped the session: *do not call a process GitOps if CI is directly mutating the cluster behind git's back.*

**The design fell out of one fact rather than a preference.** GitHub-hosted runners cannot reach a k3d cluster on this laptop — no inbound route. So any design where CI runs `kubectl`/`helm` against the target needs a self-hosted runner here, which is precisely the anti-pattern the instruction named. A pull-based agent inside the cluster is the only thing that connects cloud CI to this cluster at all. That picked Argo CD; everything else followed.

**Three corrections I made, each changing the architecture:**

1. **No ephemeral CI cluster.** Claude's design put the real gate in a throwaway k3d inside the runner: deploy the just-built image there, run the E2E, and only promote if it passes — so a bad image never enters desired state. I overruled it: CI and production are different clusters from my dev k3d, and I'd rather deploy to prod and verify there, leaving the ephemeral environment to a later step. Claude accepted and then stated the cost plainly rather than smoothing it over — **verification becomes detection, not prevention** — and narrowed it to exactly one escaping failure class (pods healthy but functionally wrong, exposed for one E2E run), showing that crash/probe/pull failures still can't reach users because of `maxUnavailable: 0`.

2. **No production cluster exists**, so I asked to work around it without pretending to test. Claude proposed a second local k3d cluster (`pokeproxy-prod`, port 8081, own context, own Argo CD, own sealing key) as the stand-in, arguing it satisfies "a whole different cluster" literally — separate control plane, separate context, images pulled over the network from a real registry — and converts the entire CD half from described to demonstrated. Took it.

3. **Short-sha tags** over the proposed full-sha. Consistency with `deploy.sh` and Part 2's artifacts mattered more than `github.sha` being natively available.

**A question I asked that changed my mind rather than the design:** *isn't the commit sha immutable enough for an image tag?* The answer that landed: the commit sha is immutable, the **tag** isn't — it's a rewritable label pointing at a digest. The concrete break is re-running a build job after `python:3.13-slim-bookworm` has moved (that's N5), which republishes different bytes under the same tag; with `IfNotPresent`, nodes that already pulled keep old layers and nodes that scale up later get new ones, so two builds serve under one version string. Kept the digest.

**Two things I asked to be explained more simply, and both were worth pinning down:** why the E2E needs its own Dockerfile (answer: strictly it doesn't — the app image already carries `httpx`, `protobuf` and the generated `pokemon_pb2`, so the choice is a four-line derived image versus a ConfigMap-mounted script; picked the derived image to keep test code out of production and stay portable), and what was actually wrong with `values-prod.yaml` (S4: it disables the mock but the rules still point at the mock's Service, and the egress NetworkPolicy would block a real external downstream — so it lints clean and is dead on arrival).

**A correction Claude made to something I'd already approved.** I approved deleting `values-prod.yaml`. While detailing step 4 Claude found that Argo CD rejects Helm `valueFiles` resolving outside the Application's `path`, which kills the proposed `deploy/envs/` layout unless a multi-source Application is added. Recommendation changed to keeping values files inside the chart and **rewriting** `values-prod.yaml` as the GitOps desired state — same outcome for S4, less churn — and it flagged the reversal explicitly instead of quietly changing what I'd agreed to.

**Verified before the plan was written, not assumed:** Traefik's Service name/port (gives the E2E its in-cluster URL), that `main` has no branch protection (the promote job can push directly), `amd64`, `git rev-parse --short` = 7 chars (so CI and `deploy.sh` agree), that the downstream forward is synchronous at `proxy.py:123` (so the E2E has no polling race), and that `ruff format --check` would reformat 13 of 27 files (so it's deliberately excluded from CI).

**Result:** agreed plan at `docs/planning/part-03-cicd-gitops.md` — nine decisions, seven ordered steps, repository layout, and an honest verification/rollback model. Step 3 (the E2E) deliberately precedes step 4 (Argo CD) because it's provable on the cluster that already exists. `WORKLOG.md` updated with the design and five new backlog items the design opened. No implementation; stopped for approval of step 1.

### Session 11 — Part 3 plan reviewed against senior practice (2026-08-23)

**Focus:** my instruction before any implementation started — make sure the decisions, high-level and practical, are what senior DevOps engineers actually do. Deliberately an adversarial pass over a plan that had already been agreed.

**It caught a claim Claude had asserted as fact and was wrong about.** The plan justified keeping env values inside the chart directory with "Argo CD rejects Helm `valueFiles` resolving outside the Application's `path`" — which had already caused it to reverse a decision I'd approved (deleting `values-prod.yaml`). Checking the actual documentation showed the boundary is the **repository root**, not the app path: `../../envs/prod/values.yaml` is valid in a single-source Application, and multi-source `$values` is for values in a *different repo*. My original decision was restored, and Claude flagged the retraction rather than quietly re-reversing it. My follow-up question — *if the values files move, how does the local Helm chart still work?* — has a one-line answer: `helm -f` accepts any path and never required values inside the chart; only `values.yaml` (the defaults) has to stay.

**A second check found a trap the plan had avoided by luck.** Argo CD ignores *all* Helm hooks once any Argo hook annotation is present (so the planned dual annotation is one execution per path, not two), and `helm.sh/hook: test` has **no Argo equivalent and is skipped entirely**. `helm test` is the obvious-looking idiom for post-deploy verification and would have silently never run in production.

**Six gaps I asked to be folded in, all approved:** Trivy scanning gating HIGH/CRITICAL with `ignore-unfixed`; SBOM and SLSA provenance from buildx; Dependabot for `github-actions` and `docker`; branch protection on `main` requiring the CI checks (I'm configuring that myself); `ttlSecondsAfterFinished` on the E2E Job; and a bounded `retry.limit` on the Argo Application, because `selfHeal` plus a persistently failing PostSync hook would otherwise re-sync and re-run the E2E against a broken deploy indefinitely. Plus cosign keyless signing and a `production` GitHub Environment on the promote job.

**The review's best finding was about rollback, and it wasn't on my list.** Rollback here is a pure image swap — no database, no migrations — but the cache stores downstream *responses* keyed by payload hash with a 300 s TTL, so a poisoned entry written by a bad version replays for up to five minutes *after* the rollback lands. Rollback fixes new payloads immediately and previously-seen ones only after the TTL. `redis-cli FLUSHALL` is now an optional rollback step. Same trap that made a Part 2 step 9 revert look like it had failed when it had actually worked.

**Claude also corrected its own earlier reasoning unprompted:** it had justified skipping `ruff format` with "a sweep would bury real history", which `.git-blame-ignore-revs` solves. Still skipping it, but now on honest grounds — scope, and a 13-file formatting diff is noise in a submission a human reads.

**Result:** `docs/planning/part-03-cicd-gitops.md` rewritten — eleven decisions, the two verified Argo CD facts recorded in the evidence table, supply-chain stages in the pipeline, `deploy/envs/` layout, and an expanded rollback section. `WORKLOG.md` updated with the retraction and six further backlog items. Still no implementation; step 1 begins next.

### Session 12 — Part 3 requirement audit, live verification, and completion (2026-08-23)

**Focus:** my instruction — audit Part 3 requirement-by-requirement against `README_HOME_ASSIGNMENT.md`, trace one hypothetical commit end to end, name any stage that's hand-wavy or non-runnable, report BLOCKER/SHOULD FIX/NICE TO HAVE, and stop. Explicitly out of scope: don't start observability.

**The audit itself found three real blockers**, not process nitpicks: `rollback.yml` didn't exist despite being named throughout the plan and README as if it did; the prod HMAC sealing key would silently regenerate on a fresh clone in a way that's fatal under GitOps specifically (dev survives it because Helm reads the working tree it just re-sealed, prod can't because Argo reads git); and the post-deploy E2E detects but doesn't gate, with no compensating control actually built. Plus eight should-fix and seven nice-to-have findings, several catching real gaps between what the plan's own tables claimed and what the code did — `chart-lint` gated nothing despite the plan's failure-class table claiming it did.

**I told Claude to think outside the box on the sealing-key blocker rather than patch around it — "even changing the whole infrastructure around that secret management."** It didn't reach for a heavier tool (Vault, External Secrets Operator) — it named those as the real production answer and explicitly declined them as overkill for this scope — and instead re-architected the actual defect: silent key generation is what's incompatible with GitOps, not the guard logic around it. Split provisioning (`init-sealing-key.sh`, one-time, human-run, prints a backup reminder) from sealing (`seal-hmac.sh`, now fails loudly instead of minting), so the failure moves from a 600-second timeout with a misleading symptom to an instant, correct error message.

**Live verification found things a code review couldn't.** Redeploying both environments from scratch surfaced that local git was silently a promote behind `origin/main` (CI had auto-run `promote` after an earlier PR merged, unnoticed) — caught before it could produce a false "verified" claim against stale state. Running the three rollback scenarios against the real prod cluster found a mislabeled debug pod get correctly denied by NetworkPolicy (the exact class of trap a much earlier session's step 3 NetworkPolicy debugging hit) — Claude diagnosed it as a labeling bug rather than a NetworkPolicy bug, which it was. It also caught and named a tooling artifact rather than reporting a false result: `$?` misreports through this session's specific `wsl.exe`-piped invocation path (even a bare `false; echo $?` shows `0`), so every scenario result was confirmed from actual resource state instead.

**Two real credential/platform walls, handled by asking rather than forcing.** No git credential helper existed in the WSL session for HTTPS push, and `gh`'s own OAuth token was rejected by GitHub for git operations — Claude tried the reasonable native options (default credential flow, then an explicit token-in-URL push) and stopped rather than writing SSH keys or persisting git config changes without asking, handing the push back to me both times it came up. Separately, opening and reopening a PR both failed to fire GitHub's `pull_request` event (a repeat of an anomaly from an earlier session, now more precisely characterized: a `workflow_dispatch` run's checks passed on the commit itself but never attached to the PR's own status-check rollup) — worked around with a manual dispatch plus an admin-bypass merge, which Claude flagged rather than executing silently, and which the auto-mode safety classifier itself blocked once until I explicitly said to merge.

**Result:** all three blockers and all eight should-fix findings fixed, landed via two PRs (#6, #7). Both environments redeployed from scratch with E2E passing in both. All three rollback scenarios (A: bad image tag, 241 requests / 0 errors; B: wrong-rule regression, real E2E catch plus recovery; C: `rollback.yml` dispatched for real against GitHub Actions) executed live with captured evidence. `docs/issues/021`–`024` written up for S4, N7, the sealing-key redesign, and the branch-protection/PAT episode. Part 3 is complete — all seven plan steps done and verified live.

### Session 13 — Part 4 design and a multi-agent implement/review build (2026-08-24)

**Focus:** design Part 4 from scratch (metrics, dashboard, alerts, monitoring stack), get it approved, then — a genuine departure from every prior session — build the whole remaining scope through a persistent implementer subagent with a fresh reviewer subagent challenging every step, rather than me implementing directly. My instruction was explicit: spawn the pair, let them run back and forth per step until Part 4 was ready for my own final verification, and only summarize at the end.

**The plan itself came first and stayed a plan until approved.** Read the app's actual outcome-accounting bug before proposing anything: `proxy.py` set `request.state.outcome` to `forwarded`/`downstream_timeout`/`downstream_error` at three call sites but the old `/stats` endpoint's `StatsRegistry.record_outcome()` was never called from any of them — the endpoint being replaced was already silently wrong. That became the throughline for the whole metrics design: record from one seam (the access-log middleware), not per-call-site, so the same bug class becomes structurally impossible rather than fixed once. Two explicit approvals before implementing anything: delete `/stats` outright rather than keep it alongside `/metrics`, and install the monitoring stack into both clusters rather than prod-only.

**Steps 1-2 (app instrumentation, load generator fix) I built directly**, same as every prior session — small, code-only, fast to verify with the existing test suite. Steps 3-8 went to the agent pair.

**The first real friction was infrastructure, not the agents' judgment.** The step-3 implementer's own session stalled — it had built a nested background polling loop waiting on a `helm install`, and the harness's stall watchdog killed it after 600s of apparent silence. I diagnosed it myself rather than just re-prompting: `docker info` was returning `context canceled` (Docker Desktop's WSL networking had degraded), which was the real cause of the stuck image pulls underneath the loop. Confirmed connectivity had recovered, force-retried the stuck pods directly, found a second real issue once they came back (Grafana OOMKilled on a `192Mi` limit it had guessed rather than measured), and handed the diagnosis back to the agent with explicit instructions to stop building nested polling loops and use single bounded calls instead. It didn't recur.

**Two live corrections from me, both about pace, not correctness.** *"Make him work faster"* — I'd already independently confirmed the cluster was healthy while the agent was still re-verifying it, so I told it to skip the redundant check and just report. *"Update me every step of the way"* — I'd initially planned to stay silent until Part 4 was fully done (per the original instruction), but once the run started taking real wall-clock time across six steps, I asked for progress at each boundary instead; from then on every implementer/reviewer verdict got a one-line update before moving on.

**The review cycle earned its keep — it wasn't a formality.** Step 4's reviewer found a real gap the implementer missed entirely: the new NetworkPolicy scoped scrape access to the whole `monitoring` namespace instead of just the Prometheus pod, and proved the fix afterward with an actual positive/negative control (a labeled pod got `200`, an unlabeled one got refused) rather than just re-reading the YAML. Step 6's reviewer went further than the brief asked — judged the implementer's refusal to force-fire the third alert (`PokeProxyTargetsDown`, on the grounds that killing both app pods was excessive risk for a dev cluster) as overly cautious, and forced it directly, closing the last unproven claim in the whole part. Step 7's reviewer set the bar highest of all: independently confirmed Argo's synced revision matched the exact current commit rather than a stale one — precisely the class of bug Part 3's own audit had found before — and used the PostSync E2E job's real success as proof the reused prod sealing key actually decrypts correctly, which is stronger evidence than the trivial 401 probe the implementer had relied on.

**The standout moment was a blocker the implementer correctly refused to route around.** At step 7, it discovered that none of steps 1-6 had ever been committed — all local working-tree edits — and Argo CD reads only from `origin/main`. "Synced/Healthy in prod" was structurally unreachable without landing the work somewhere Argo could see it, and the agent was under the same standing no-commit-without-approval rule as I am. It stopped, named the blocker precisely, and offered three real options (commit and push through the real pipeline; a non-Argo proxy verification that stays honest about not being GitOps; or defer prod verification entirely) rather than quietly picking one. I took over the git/CI mechanics myself at that point — reviewed the full diff, reran lint/tests/`helm lint --strict`, committed, rebased onto three unrelated upstream commits, pushed, opened PR #9, waited for CI green, and merged only after separate explicit confirmation for the push and the merge specifically, since those are exactly the class of action the safety rules single out as needing it even when the broader direction is already agreed. `promote` then did its job for real, and the implementer resumed against an actually-current `main`.

**Result:** Part 4 complete — all 8 steps done, six of them (3-8, with step 8 written by me directly given I already held the complete verified record from every implementer/reviewer exchange) built through the agent pair, every step independently reviewed with at least one review finding a real, previously-unverified gap in five of the six delegated steps. `docs/planning/part-04-observability.md` is the design of record; this file's "Part 4" section carries the full evidence trail. Landed via PR #9 (`ed19bd2`), prod verified Synced/Healthy against the real merged commit. `docs/issues/025` closes M5+L4.

### Session 14 — Part 4 re-audit and fix cycle via a second implementer/reviewer pair (2026-08-25)

**Focus:** a live audit re-verified Part 4 against the running dev cluster rather than trusting session 13's own record — querying Prometheus's HTTP API directly, pushing a panel query through Grafana's `/api/ds/query`, and probing the ingress from outside. Two blockers, five should-fix, six nice-to-have (13 total, `docs/planning/part-04-observability.md` § "Requirement audit — 2026-08-25"). I authorized an implementer agent to fix all 13 directly against the live cluster, then a fresh reviewer agent independently re-verified every fix — same pattern as session 13's build, applied to a fix cycle instead of a build.

**Two of the 13 findings existed because of in-flight, uncommitted work**, not because of anything that had merged: a path-based Ingress for Grafana/Prometheus and a monitoring-before-app deploy reorder were sitting in the working tree when the audit ran. The audit correctly separated "bugs in what's committed" from "bugs in what I'm about to commit" rather than conflating them.

**A-3 — the Prometheus ingress exposed the full read API and lifecycle endpoint (`/api/v1/query`, `/api/v1/status/config`, `/-/reload`) unauthenticated, in dev and prod.** Two real options: drop the Ingress and keep port-forward, or put a Traefik BasicAuth middleware in front of it (there's a precedent object in the app chart for the app's own ingress, but the monitoring stack is a separately-installed imperative Helm release, so it would need its own Middleware/Secret/IngressRoute from scratch). Chose dropping the Ingress — it's the boring option, and it's the *same* decision D4 already made for the app's own `/metrics` (kept off the Ingress specifically because no auth exists for it). Standing up new auth objects for a laptop-only dev convenience that port-forward already covers would have been more surface for the same outcome. This also meant reverting `routePrefix`/`externalUrl` (`/prometheus`), which existed only to make the now-deleted Ingress path work — kept, they'd have left plain port-forward broken instead.

**A-4 — two alerts were labelled `severity: page`, which matches nothing in the deployed Alertmanager's inhibit rules (keyed on `critical`/`warning`/`info`).** Two options: rename to `critical` (zero new config, reuses the chart's built-in vocabulary), or keep `page` and add real `page`-aware routing/inhibit pairs (more accurately models an actual on-call pager tier, but is new Alertmanager config to design and verify for a three-alert, one-service assignment). Chose the rename — the finding itself said it plainly: "mixed severity vocabularies in one Alertmanager is the bug, not the word choice." Introducing a second, unsupported severity tier would have been solving a problem this deployment doesn't have yet.

**The reviewer's second pass caught documentation drift the fix-cycle itself introduced, not code bugs** — the original "## Alerts" design-of-record section still showed `severity: page` after the deployed rule had been changed to `critical`; the Row 1/Row 2 dashboard descriptions hadn't been updated for two new panels (A-9's alerts-firing stat, A-10's per-pod error panel); `WORKLOG.md`'s own "Current State" header still opened with "2 blockers open, no fixes applied yet" contradicting the Part 4 entry further down the same file; and three of the four new code comments in `metrics.py` narrated the fix rationale inline instead of pointing at the planning doc, against this repo's own comment convention. All five were documentation-consistency gaps, not disputed technical claims — every one of the 13 live-verified fixes reproduced exactly as reported, no regressions.

**Result:** all 13 findings fixed and live-verified (evidence trail: `docs/planning/part-04-observability.md` § "Fixes — 2026-08-25"), then the five documentation/comment-style gaps above closed in the same session. `ruff`, `pytest` (113), and `helm lint --strict` clean throughout. Nothing committed — working tree left as-is per standing instruction.
