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
