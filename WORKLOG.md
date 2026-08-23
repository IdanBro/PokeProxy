# PokeProxy Engineering Work Log

This document is the persistent engineering state for the assignment. I update it as decisions are actually made rather than trying to design the entire solution upfront.

**Standing rule — token economy.** Maximum information in minimum cost, in every response, document and tool call. Tables over prose; lead with the answer. Never compress evidence, exact numbers, `file:line` refs, honest uncertainty, or the reasoning behind a decision — compress the packaging. Terse is the goal; vague is a failure. Defined in `CLAUDE.md`, applies to this file and everything under `docs/`.

## Current State

**Current phase:** **Part 3 — CI/CD & GitOps: complete, all 7 steps done and verified live, 2026-08-23; re-audited live 2026-08-24 with no regressions found.** Second-pass audit traced the same commit-to-rollback path against `origin/main` at `da55fc1`, this time reading every claim from a live system (`gh`, `kubectl`, `cosign`, `docker buildx imagetools` via WSL) rather than from this file's prior record: branch protection still requires `Chart lint`/`Lint`/`Test`; the last real merge (`5966025`) has a green CI run ([32668762523](https://github.com/IdanBro/PokeProxy/actions/runs/32668762523)); the current promoted digest (`pokeproxy@sha256:87a5a28e…`) is anonymously pullable and `cosign verify`-able; Argo CD in `k3d-pokeproxy-prod` is `Synced`/`Healthy` at exactly `da55fc1`; both running pods carry that exact digest; a live probe returns `401`. Full detail: `docs/planning/part-03-cicd-gitops.md` § "Requirement audit — 2026-08-24." One new finding, doc-only, **N1**: `deploy/README.md`'s Rollback section (lines 180–182) still said the three rollback scenarios were "not yet executed," contradicting this file's own record that A/B/C ran live on 2026-08-23 — missed by PR #7's step-7 doc pass. **Fixed** same session: the stale paragraph is replaced with a summary table of all three executed scenarios and their results.

**Prior state, 2026-08-23:** A requirement-by-requirement audit against `README_HOME_ASSIGNMENT.md` traced one hypothetical commit through CI → image publication → desired state → rollout → post-deploy E2E → failure handling → rollback, found 3 blockers / 8 should-fix / 7 nice-to-have (full trace and findings: `docs/planning/part-03-cicd-gitops.md` § "Requirement audit"), all of which were then fixed, merged via [PR #6](https://github.com/IdanBro/PokeProxy/pull/6), and verified live the same day: dev and prod redeployed from scratch with E2E passing in both, the F-2 fix regression-tested, and all three step-6 rollback scenarios (A, B, C) executed against the real prod cluster with captured evidence, including scenario C dispatching the real `rollback.yml` against GitHub Actions. Step 7 (issue write-ups) closed with `docs/issues/021-024`. PR #3, #4, #6 and #7 all merged to `main`. Plan: `docs/planning/part-03-cicd-gitops.md` (design `db4abba`). Step 4 is complete: 4a (the `deploy/envs/` move plus the N7 fix) and 4b (prod stand-in cluster on 8081 + Argo CD 10.4.0), both verified live. Step 5 is complete and verified live, including a real branch-protection obstacle found and fixed mid-implementation (classic PR-required rule blocked the promote push; fixed with a fine-grained PAT + `[skip ci]`, see below).

**Part 3 requirement audit, 2026-08-23 — three blockers.**

| ID | Sev | Finding | Evidence |
|---|---|---|---|
| F-1 | Blocker | `.github/workflows/rollback.yml` **does not exist**. Rollback is named in the plan's pipeline diagram, its response table (`gh workflow run rollback.yml -f sha=<last-good>`), `deploy/README.md` and this file's stack line — documented, not runnable. Scenarios A/B/C unexecuted | `ls .github/workflows/` → `ci.yml` only |
| F-2 | Blocker | **Prod HMAC secret is not reconstitutable off this machine, and under GitOps that is fatal rather than inconvenient.** Fresh clone → `seal-hmac.sh --env prod` mints a new key (`seal-hmac.sh:110`) and re-seals `deploy/envs/prod/values.yaml` **in the working tree** (`:157`). Argo reads the *committed* file, sealed against a key that cluster lacks → no decrypt → `pokeproxy-hmac` never created → `envFrom.secretRef` is non-optional (`deployment.yaml:48`) → `CreateContainerConfigError` → never Healthy → `bootstrap-prod.sh` exits 1 after 600s | code paths above |
| F-3 | Blocker | Post-deploy verification **detects, does not gate** — the assignment says "gates the deployment on it." On failure Argo marks sync Failed, retries ×3, stops. No notification, no revert, and per F-1 no rollback workflow. The trade is deliberate and argued in the plan; what's missing is that its only compensating control doesn't exist | `application.yaml`, `templates/e2e/job.yaml` |
| F-4 | Should fix | **`chart-lint` gates nothing** — no `needs`, nothing needs it; `promote` depends only on the three build jobs (`ci.yml:217`). A chart that fails `helm lint --strict`/kubeconform still promotes. The plan's failure-class table claims this class cannot reach users — **false as implemented** | `ci.yml:51`, `:217` |
| F-5 | Should fix | The **promoted** desired state is never linted: the promote commit carries `[skip ci]` (`ci.yml:265`), so chart-lint only ever sees the pre-promote file | `ci.yml:265` |
| F-6 | Should fix | Workflow-level `cancel-in-progress: true` applies to `main` (`ci.yml:9`); two fast merges cancel the older run, `promote` included. Job-level `concurrency: promote` prevents interleaving, not cancellation. A cancel between `git commit` and `git push` silently skips that sha | `ci.yml:9`, `:220` |
| F-7 | Should fix | The designed rollback covers **only the image axis**; Argo reconciles everything under `deploy/`. A chart/values regression isn't rolled back by a tag+digest rewrite — and the plan's prose says the mechanism is "a git revert," which is a different mechanism from what step 6 specifies | plan §Rollback vs §Step 6 |
| F-8 | Should fix | `activeDeadlineSeconds: 120` vs the E2E's own `STARTUP_MAX_WAIT_SECONDS = 90`. On the Helm path an 85s rollout leaves the assertions 30s → a slow-but-healthy deploy fails the E2E and reads as a bad deploy | `job.yaml:20`, `e2e_check.py:30` |
| F-9 | Should fix | E2E Job declares **no resource requests/limits**, unlike every other workload in the chart | `job.yaml` |
| F-10 | Should fix | **Signing is write-only** — three images cosign-signed, nothing verifies at pull time. Argo's `signatureKeys` is git-commit signing, not images. `cosign verify` was run by hand once, out-of-band | `ci.yml:121`, `:167`, `:213` |
| F-11 | Should fix | A red PR can still merge to `main` — `required_status_checks` carries no contexts (DoD #2). Pipeline stays safe (`promote` needs the builds), but broken code lands on `main` with no promotion | **not re-verified this session — `gh` is not on PATH in the audit shell**; rests on this file's earlier record |
| F-12 | Nice to have | Stale plan text | **Fixed** — corrected in place in the plan |
| F-13 | Nice to have | No `docs/issues/021+` for S4/N7 | **Fixed** — `docs/issues/021-values-prod-undeployable.md`, `022-seal-hmac-wholesale-rewrite.md` |
| F-14 | Nice to have | promote doesn't assert its digests are pullable | Open |
| F-15 | Nice to have | `already_sealed()` never confirms the ciphertext decrypts under the reused key | Open |
| F-16 | Nice to have | No rebase-conflict handling in promote/rollback | Open |
| F-17 | Nice to have | Fork PRs can't run `build-*` | Open |
| F-18 | Nice to have | The branch-protection/PAT episode had no issue write-up | **Fixed** — `docs/issues/024-branch-protection-pat.md` |

**Live from-scratch verification, 2026-08-23 (WSL/Ubuntu, real tools — `helm`/`kubeseal`/`gh` are not on the Windows Git Bash PATH used earlier).** User updated branch protection independently: `required_status_checks` now lists `Chart lint`, `Lint`, `Test` — **F-11 closed**, not by a code change.

**Dev: torn down and redeployed clean.** No k3d clusters existed at session start. `deploy.sh` end to end: cluster created, all three images built at `b281080`, all 4 pods Running, Helm `--atomic` E2E hook passed (no rollback triggered). Independently confirmed via `/received` rather than trusting the hook's own exit code: `e2e-f559ed17` delivered through the real proxy path.

**F-2 regression test, isolated and reversible.** Moved `.secrets/sealing-key-local.yaml` aside, ran `seal-hmac.sh --env local`: it printed the new fail-loud message and **stopped** — no `Installing sealed-secrets controller`, no `Using sealing key at`, nothing past the guidance text. Restored the key; `git status` on `deploy/envs/local/values.yaml` showed no diff, confirming it was never touched. (The script's own `$?` reported 0 through this specific `wsl.exe`-piped invocation path — traced to a genuine tool-chain artifact, not a script bug: even a bare `false; echo $?` misreports `0` through the same `wsl.exe -d Ubuntu -- bash -c '...'` path, while the identical construct in native Git Bash correctly reports `1`. Judged on actual side effects, which is the evidence that matters, not the misreported code.)

**Prod: torn down and redeployed clean, against real `origin/main`.** `bootstrap-prod.sh` end to end: cluster created, sealing key reused correctly (`Using sealing key at .../sealing-key-prod.yaml`), Argo CD 10.4.0 installed, Application applied and converged to **Synced / Healthy** in under 90s, PostSync E2E Job `Completed`. Independently confirmed via `/received`: `e2e-6eb20224` delivered.

**A real gap this surfaced: local git was one promote behind, silently.** `origin/main` had moved to `de26681` (`chore(deploy): promote b281080 [skip ci]`) — CI auto-ran `promote` after PR #5 merged, sometime before this session, and nothing local had fetched it. Argo correctly pulled the current `origin/main` regardless of local git state (as designed — it reads GitHub, never the working tree), so the prod cluster's running digest (`pokeproxy@sha256:ca854026…`) matched `de26681`'s values file exactly, not the older `a365e7f`-era digest a prior WORKLOG entry recorded. `git pull --ff-only origin main` afterward, clean fast-forward, zero conflict with this session's uncommitted local fixes (different files). Worth naming plainly: **this session's local prod deployment verified `origin/main` as it actually stands today, not the pending local fixes (F-1, F-4–F-10) — those are still uncommitted and Argo has no way to see them until pushed.**

**Status: DoD item "e2e tests passing in both dev and prod" — met, against current real state.** Both environments confirmed live, from a clean teardown, independently verified past the hook's own reported result.

**Step 6 scenarios A and B — executed live against the real prod cluster, 2026-08-23.** Argo's `automated` sync policy was temporarily removed via `kubectl patch` (user-approved) so a controlled bad state could be pushed directly, without needing a git commit for a throwaway test; re-enabling it afterward let `selfHeal` prove the recovery rather than me asserting it.

**Scenario A — rollout failure.** `kubectl set image` to a tag that doesn't exist (`pokeproxy:scenario-a-does-not-exist`), load generator running against `localhost:8081` throughout (using the real app image's own venv via `docker run --entrypoint python`, since `uv`/the app's deps aren't on this host — reused rather than reinvented). Result: new pod stuck `ImagePullBackOff` for the full 74s observed; both old pods stayed `Running` the entire time (`maxUnavailable: 0` doing exactly its job — Kubernetes never touches the old ReplicaSet until a new pod is Ready, which this one never became). **Load generator: 241 requests, 0 errors, 0.0% error rate.** Recovered with `kubectl set image` back to the known-good digest.

**Scenario B — verification failure.** No new image needed — same running image, `rules.json`'s first `reason` field overridden to `"WRONG REASON - scenario B"` via a direct ConfigMap edit + `rollout restart` (rules are read once at startup, so a restart is required to pick it up — H1's known behavior, exploited deliberately here). Pods came up healthy and Ready; ran the real `pokeproxy-e2e:b281080` image as a one-off pod with the same `envFrom: secretRef: pokeproxy-hmac` the real hook uses. **First attempt hung with no output** — traced to my own test pod carrying the wrong labels (`app.kubernetes.io/component: scenario-e2e` instead of the chart's real `app.kubernetes.io/name: e2e` + `app.kubernetes.io/instance: pokeproxy`), so the `allow-e2e-egress-to-*` NetworkPolicies correctly denied it — an unplanned but genuine confirmation that default-deny NetworkPolicy holds even against an ad-hoc debug pod, the same class of trap step 3's original NetworkPolicy debugging hit. Relabeled correctly and reran: **`phase=Failed exitCode=1`**, `{"result": "fail", "error": "expected reason 'strong fire pokemon', got 'WRONG REASON - scenario B'"}` — the exact assertion the plan says this check exists to catch. Restored the real rules, rollout-restarted, reran the same pod: **`phase=Succeeded exitCode=0`**, all 5 checks passed. Exposure window for a single check run once pods are Ready: ~10s (dominated by the check's own HTTP round trips, not by anything system-side) — consistent with prior E2E timings in this file; the multi-minute wall-clock time this attempt actually took was entirely the NetworkPolicy debugging detour, not the failure-detection latency itself.

**Recovery verified structurally, not just by inspection.** Re-enabled `syncPolicy.automated` and force-refreshed: Argo re-synced to **Synced / Healthy**, spun up a *new* ReplicaSet (`696bd6bf8d`) confirming it retook real ownership rather than accepting my manual edits, and the rules ConfigMap read back exactly the chart-rendered good values — `selfHeal` did the restoring, not a manual undo.

**`$?` is not trustworthy through this session's `wsl.exe`-piped invocation path** — confirmed independently earlier and it recurred here (a function's `return $ok` reported wrong). Every result above is read from actual resource state (`kubectl get -o jsonpath`, `kubectl logs`) captured in separate, deliberate calls, not from a captured exit code.

**Scenario C — not yet run.** Needs `rollback.yml` and today's should-fix batch to actually exist on GitHub; both are still local-only as of this entry. Landing them (branch + PR, user-approved) immediately follows this entry.

**Landed and verified live, 2026-08-23.** [PR #6](https://github.com/IdanBro/PokeProxy/pull/6) (`fix/part3-audit-blockers`, commit `810d9e0`) pushed and merged by the user (`4d74e37`) — I hit a genuine credential wall pushing it myself (no git credential helper configured in this WSL session; `gh`'s own OAuth token was rejected by GitHub for HTTPS git push with `Invalid username or token`, consistent with why the promote job needs a dedicated fine-grained PAT rather than a bare token) and handed it back rather than writing SSH keys or persisting new git config without asking.

**CI on the merge: 7/7 green**, run [32666613159](https://github.com/IdanBro/PokeProxy/actions/runs/32666613159) — including `Promote`, which is the live proof F-4/F-5 work: `chart-lint` is now a real dependency of `promote`, and `promote`'s own lint-before-commit step passed against real output. Promote wrote `073d78e` (`chore(deploy): promote 4d74e37 [skip ci]`).

**Prod redeployed against the merged state**, digest `sha256:cf31e472…` confirmed on the running pods, E2E passed (`e2e-2270e9c8` delivered). This is the first time F-8 (`activeDeadlineSeconds: 180`) and F-9 (E2E resource requests/limits) have run for real rather than just rendered.

**Scenario C — executed live.** `gh workflow run rollback.yml -f sha=b281080`, run [32666881696](https://github.com/IdanBro/PokeProxy/actions/runs/32666881696), **succeeded**. Verified, not assumed:
- Commit `9452d0e` (`revert(deploy): roll back to b281080 [skip ci]`) landed directly on `main`, no PR — same push path as `promote`.
- All six written digests match the `b281080`-era values exactly (`pokeproxy@sha256:ca854026…`, `mock-downstream@sha256:8b9c52cc…`, `pokeproxy-e2e@sha256:3f93394a…`).
- **No second CI run** — `gh run list` shows only the original merge's `CI` run and the `Rollback` run; `[skip ci]` held under the same PAT-push conditions step 5 already proved this for.
- `bootstrap-prod.sh` reconciled it: Synced/Healthy, running pod digest `sha256:ca854026…` matches byte-for-byte, PostSync E2E passed (`e2e-af6c6c8f` delivered), and a live probe against `:8081/stream` returned the expected `401` — the app is healthy and serving correctly on the rolled-back version, not merely "Synced" on paper.

**All three Part 3 rollback scenarios are now executed with captured evidence.** DoD item 9 closes.


**Blockers and should-fixes from the 2026-08-23 audit — implemented, none verified live yet (no `helm`/`kubeseal` in this shell; needs a real run against `k3d-pokeproxy-prod`).**

| ID | Fix | Files |
|---|---|---|
| F-1 | New `rollback.yml`, `workflow_dispatch` input `sha`. Resolves the three images' digests from GHCR via `docker buildx imagetools inspect` (the same tool used to hand-verify step 2's images), writes them into `deploy/envs/prod/values.yaml` with the same `yq` pattern as `promote`, **lints and kubeconforms the result before committing** (folds in F-5 for this path), commits `revert(deploy): roll back to <sha> [skip ci]` via the existing `PROMOTE_PUSH_TOKEN`, pushes to `main` | `.github/workflows/rollback.yml` (new) |
| F-2 | **Re-architected rather than patched.** The root cause wasn't a missing guard — it was that `seal-hmac.sh` silently minted a fresh sealing key whenever none was found, which is incompatible with GitOps: a fresh clone would mint a key that cannot decrypt the ciphertext already committed to `deploy/envs/prod/values.yaml`, and the mismatch would only surface ~600s into `bootstrap-prod.sh`'s converge loop as `CreateContainerConfigError`. Split provisioning from sealing: new `scripts/init-sealing-key.sh --env {local,prod}` is now the *only* place a key is ever generated — one-time, human-run, refuses to run twice against an existing key, prints a hard reminder to back the file up before doing anything else. `seal-hmac.sh` lost `generate_sealing_key()` entirely; if the key file is missing it now **exits 1 immediately** with the remediation (`init-sealing-key.sh`, or restore your backup) instead of quietly generating one and re-sealing over a mismatch. Moves the failure from 600s deep and silent to instant and explicit | `scripts/init-sealing-key.sh` (new), `scripts/seal-hmac.sh` (removed silent-mint path, removed now-unused `openssl` requirement), `scripts/deploy.sh` + `scripts/bootstrap-prod.sh` (dropped the `openssl` requirement, now unused directly), `deploy/README.md` (new step 0, step 3/4 rewording) |
| F-4 | `promote.needs` now includes `chart-lint` — a chart that fails `helm lint --strict`/kubeconform can no longer promote | `.github/workflows/ci.yml` |
| F-5 | `promote` now lints + kubeconforms `deploy/envs/prod/values.yaml` itself, after the `yq` write and before the commit — the file Argo actually consumes is validated, not just the pre-promote one | `.github/workflows/ci.yml` |
| F-6 | Top-level `concurrency.cancel-in-progress` is now `${{ github.ref != 'refs/heads/main' }}` — a run on `main` (which may contain a live `promote` push) is no longer cancellable by the next merge; PRs and manual dispatches keep cancel-on-superseded | `.github/workflows/ci.yml` |
| F-7 | Doc-only: `deploy/README.md`'s Rollback section now states plainly that `rollback.yml` covers the image axis only — a chart/values/manifest regression needs a plain `git revert <merge-commit>` through the normal PR path instead. Previously implied a single "git revert" mechanism covered both | `deploy/README.md` |
| F-8 | `activeDeadlineSeconds` on the E2E Job: 120s → 180s, so the script's own 90s startup budget doesn't eat the whole deadline on a slow-but-healthy rollout | `deploy/helm/pokeproxy/templates/e2e/job.yaml` |
| F-9 | Added `resources` (50m/64Mi request, 100m/128Mi limit — matched to mock-downstream's footprint) to the E2E Job container, closing the one workload in the chart with none | `deploy/helm/pokeproxy/values.yaml`, `templates/e2e/job.yaml` |
| F-10 | Doc-only: `deploy/README.md` now states explicitly that cosign signing is write-only today — nothing on the pull side verifies before a pod runs — and names the real fix (an admission policy) rather than implying the chain has a consumer | `deploy/README.md` |

**Verified this session:** `bash -n` clean on all four touched shell scripts; plain-YAML files (`rollback.yml`, `ci.yml`, `values.yaml`) parse with PyYAML. **Not verified:** `job.yaml`'s Helm templating (no `helm` binary in this shell), any live `helm lint`/`kubeconform`/`yq` behavior, and none of this has run against a real cluster or a real GitHub Actions run yet. F-11 (branch protection) was not re-checked live either — `gh` is not on PATH here.

**F-3 has no code fix — it's a documented, deliberate trade (see plan's "Where verification actually sits"), and F-1 landing is what closes its compensating-control gap once verified live.**

**The F-2 framing correction matters more than the finding.** This file's backlog currently records the prod sealing key as "the same accepted trade-off already documented for dev." It is not. Dev works because Helm reads the re-sealed **working tree**; prod fails because Argo reads **git**. Same gitignored file, opposite outcome. It also blocks Part 5's clean-machine one-command bootstrap, so it needs a decision before Part 5 rather than after.

**Also corrected during the audit, in `docs/planning/part-03-cicd-gitops.md` itself:** the Final-pipeline diagram still claimed `GITHUB_TOKEN push => no workflow recursion` (the shipped mechanism is a PAT + `[skip ci]`), step 5 still said `needs: [scan, sign]` (no such jobs — scan and sign are steps inside the build jobs) and still proposed a ruleset bypass for the `github-actions` App (proven impossible on a personal repo). The Definition of done, previously 11 × "Not yet," is now a status table: 7 done, 1 done-with-caveat, 1 partial, 2 not done.

**Step 4b verified live, 2026-08-23 — and the verification found a third real bug, in the gate itself.**

**The bug: `bootstrap-prod.sh` exited 0 and printed "Done" while the Application pointed at a branch that does not exist.** Found by deliberately running the fail path rather than assuming it worked — the first background run had been killed mid-loop, so its exit 0 was a pipeline artifact and proved nothing. Root cause: `kubectl apply` changes `targetRevision`, but Argo CD has not re-evaluated yet, so `.status` still holds the **previous successful sync's** result. The loop sampled that stale status on its first iteration and passed instantly. Same class as step 3's hook-timing race: reading a condition before the system has had any chance to react to the change that was just made. This matters well beyond bootstrap — it is exactly how a promote or rollback gate reports success against the state it just replaced.

**Fix:** after applying the Application, stamp `argocd.argoproj.io/refresh=hard` and treat the annotation's *presence* as "not yet evaluated" — the controller removes it once it has re-reconciled, which makes "Argo has seen my change" observable rather than assumed. The pass condition is now: refresh annotation consumed **and** `Synced` **and** `Healthy` **and** no `*Error` condition. Re-tested: exit **1**, with the real cause surfaced — `ComparisonError: ... unable to resolve 'no-such-branch-xyz' to a commit SHA` — plus pod state. The `refreshing` line now appears in successful runs too, so the gate is visibly engaging rather than silently absent.

**`health=Healthy` is not a deploy gate, twice over.** An Application with zero synced resources reports Healthy because nothing in it is unhealthy; and an Application whose git revision fails to resolve *also* reports Healthy, because the already-running pods are fine. In both failure modes health was green. Only `sync` plus the error conditions distinguish them.

**Two smaller changes to the same loop, both prompted by trying to test it:** the converge budget is now `CONVERGE_TIMEOUT_SECONDS` (default 600) instead of a hardcoded `60 × 10s` — a fail path that takes ten unconfigurable minutes to report is a fail path nobody exercises — and the loop prints only on state *change*, replacing ~60 identical lines with 2.

| Check | Result |
|---|---|
| `bootstrap-prod.sh` from scratch | cluster + sealed-secrets + Argo CD + Application in one command, exit 0 |
| Argo CD status | **Synced / Healthy**, 4/4 pods, 0 restarts |
| Image references on running pods | `spec` **and** `status.imageID` both `ghcr.io/idanbro/pokeproxy@sha256:8b89ddd6…` — digest-pinned, pulled from GHCR, not `k3d image import`. Redis correctly stays on the tag path (`redis:7-alpine` → `docker.io/library/redis@sha256:ff02b58f…`) |
| PostSync E2E Job | ran and **passed** under Argo CD: `PostSync Job/pokeproxy-e2e -> Synced \| Reached expected number of succeeded pods`, operation `Succeeded`. Read from Argo's own `operationState`, not inferred from the Job's absence — the Job is deleted by `HookSucceeded`. The dual Helm/Argo hook annotation works in practice, one execution, no double-run |
| Independent evidence the E2E really delivered | prod mock `/received` holds exactly 1 record: `{"pokemon": {"name": "e2e-6a83994a", …}, "reason": "strong fire pokemon"}` — the unique per-run name, through the real ingress |
| Signed traffic to `localhost:8081/stream` | `load_generator.py`, 4 sent, **0 errors** |
| **`selfHeal`** | hand-scaled the live Deployment 2 → 1; Argo restored it to 2 in **under 5s**. Worth distinguishing: `timeout.reconciliation: 30s` governs git polling, while live drift is caught by the controller's resource **watch** — the two are unrelated, and only the former is affected by my tuning |
| Idempotent re-run | `application … unchanged`, converged immediately, exit 0 |
| **Fail-loudly path** | exit **1** with root cause and pod state (after the fix above) |
| Both clusters simultaneously | dev `localhost:8080` → 401, prod `localhost:8081` → 401, distinct contexts, dev pods untouched at 0 restarts |
| CI run [32640298921](https://github.com/IdanBro/PokeProxy/actions/runs/32640298921) | all **6 jobs green**, including the new `Build e2e` (36s) and the prod `Chart lint` |

**A prediction of mine that was wrong, corrected here because it changes what an operator has to do:** I expected the new `pokeproxy-e2e` GHCR package to be created **private** (as `pokeproxy` and `mock-downstream` were after step 2, needing a manual UI flip). It wasn't. Verified anonymously rather than through my authenticated Docker client — fetching a GHCR pull token with no credentials and requesting the manifest returns **HTTP 200** for all three packages. No operator action was needed.

**Still open from step 2, unchanged:** branch protection on `main` requires no status checks yet (`required_status_checks` is null) — the check-requirement half of D11, and a prerequisite for step 5's promote-job bypass.

**Step 5's first live push found the plan's "no bypass needed yet" premise had already changed underneath it.** Branch protection on `main` gained `required_pull_request_reviews` (and `required_status_checks.strict: true`) between writing the step-5 proposal and pushing it — the merge-triggered `promote` job failed immediately: `GH006: Changes must be made through a pull request`. `enforce_admins: false` does not exempt this: it only exempts a human pushing with their own admin-authenticated credentials, never an App-token push, so dropping `required_approving_review_count` to 0 (tried first) did not help — the object's mere presence still means "a PR is required," independent of the count.

**Two real fixes tried and ruled out before landing on one that works, each for a reason worth recording:**
1. **Ruleset `Integration` bypass actor for the `github-actions` App** (id `15368`, confirmed via `gh api apps/github-actions`) — the API rejected it: `"Actor GitHub Actions integration must be part of the ruleset source or owner organization"`. This repo is user-owned, not org-owned; App-type ruleset bypass actors require org context that a personal repo cannot provide. A hard platform limitation, not a config mistake — confirmed by reading the actual 422, not by assuming rulesets would work.
2. **promote opens and self-merges its own PR**, viable since 0 approvals are required — technically sound, rejected on a stated design preference: the promote commit should land on `main` directly, not as an API-merged PR.

**What works: a fine-grained PAT.** Scoped to only this repo, `Contents: Read and write`, stored as the `PROMOTE_PUSH_TOKEN` secret (minted and stored by the operator — token generation and secret storage are both actions only the account owner can perform). `actions/checkout`'s `token:` input swaps it in for the job's git credentials, so the later push authenticates as an actual repo admin, which `enforce_admins: false` does exempt from the PR requirement.

**A second real bug, caught by re-reading the job's own reasoning rather than by execution.** The promote job's "why this doesn't loop" claim — GitHub suppresses `push` retriggering only for the default `GITHUB_TOKEN` — stopped being true the moment the job switched to a PAT. A PAT-authenticated push to `main` is not exempt from retriggering; unfixed, this would have built, promoted, pushed, and repeated. Fixed by adding `[skip ci]` to the promote commit subject — the one loop-prevention mechanism GitHub honors regardless of which credential authored the push, so it holds even if the PAT is later swapped for a GitHub App installation token.

**Explicitly NOT yet verified**, pending this fix landing and a live re-run: the push actually succeeding against the PAT, `[skip ci]` actually suppressing a second run, the digest in git matching the running prod pod, and the commit-to-serving measurement.

**Step 5 verified live, 2026-08-23, via PR #4 (`4944aa4` → merge `a365e7f`) after the PAT fix above.** `promote` job: 7/7 green (run [32645019846](https://github.com/IdanBro/PokeProxy/actions/runs/32645019846)), pushed `chore(deploy): promote a365e7f [skip ci]` (`495c9c9`) directly onto `main`, exactly the design constraint stated.

| Check | Result |
|---|---|
| Promote commit lands on `main` directly | `495c9c9`, no PR, no merge commit |
| No second CI run from the PAT push | confirmed against the actual run list — one run only |
| Digest in git matches the running pod | `deploy/envs/prod/values.yaml`: `sha256:442c22df88…` = `kubectl get pod -o jsonpath` on the prod pod, byte-for-byte |
| PostSync E2E on the promoted image | passed — `PostSync Job/pokeproxy-e2e -> Synced \| Reached expected number of succeeded pods` |
| Idempotency (empty diff → no commit) | implemented, not separately live-tested this round — already covered by the dry-run in the earlier entry |
| Dev cluster unaffected | `localhost:8080` still 401, 4/4 pods, 0 restarts throughout |

**Commit-to-serving, measured with an honest caveat rather than a clean claim.** Merge (`mergedAt` 14:18:17Z) to confirmed `Synced/Healthy` + passing probe on prod: **155s**. This number is **not** a passive-polling measurement — I ran `bootstrap-prod.sh` to repoint the Application from the branch to `main` right after the merge, which force-triggers a hard refresh rather than waiting for Argo's own `timeout.reconciliation: 30s` poll to notice the change on its own. Roughly 123s of the 155s is the CI pipeline itself (build ×2 in parallel + build-e2e + promote, per the run's own duration); the Argo side converged near-instantly *because* it was force-refreshed, not because that reflects the passive-polling path. A true measurement of the passive path — commit-to-serving with zero manual intervention — is still open; recording as a known gap rather than papering over it with a single number.

**A live GitHub anomaly, unexplained, worth recording rather than hiding.** PR #4's `opened` event never triggered any CI run — three independent checks agreed (`gh run list`, the check-suites API returning `total_count: 0`, and `statusCheckRollup: []` on the PR itself) after 90+ seconds. `workflow_dispatch` on the same branch/commit ran cleanly seconds later, and the subsequent push-triggered run on the merge fired normally — so this was isolated to that one PR-open event, not a broader Actions or workflow-config problem. Most plausible explanation, unconfirmed: a transient GitHub-side rate limit or anti-abuse cooldown from the burst of runs already triggered earlier in the same session (~10 runs in under 90 minutes on a personal-tier repo). Worked around with `workflow_dispatch`; not root-caused.

**The real branch-protection story, corrected from what step 5's first draft assumed.** Between writing the step-5 proposal and pushing it, `main` gained `required_pull_request_reviews` (present even at 0 required approvals) — its mere presence, not the approval count, is what blocks any direct push (`GH006`), and `enforce_admins: false` only exempts a *human* pushing with personal admin credentials, never an App-token push. Confirmed the actual constraint by testing, not by reading docs: a repo Ruleset with an `Integration`-type bypass actor for the `github-actions` App (id `15368`) was rejected outright by GitHub's own validation — `"Actor GitHub Actions integration must be part of the ruleset source or owner organization"` — a hard limitation on personal (non-org) repos, not a config mistake. Landed on a fine-grained PAT (`Contents: Read and write`, scoped to this repo only) via `actions/checkout`'s `token:` input, which authenticates as an actual repo admin and is exempted the same way `gh pr merge --admin` is. Cost of that: a standing credential with no rotation, and a stated dependency the `[skip ci]` fix required — a PAT push is not exempt from CI retriggering the way the default `GITHUB_TOKEN` is, so without `[skip ci]` this would have looped.

**Step 4b detail — prod stand-in cluster and Argo CD.**

New: `deploy/k3d/cluster-prod.yaml` (cluster `pokeproxy-prod`, port 8081, same pinned k3s `v1.35.5-k3s1` as dev), `deploy/argocd/install-values.yaml`, `deploy/argocd/application.yaml`, `scripts/bootstrap-prod.sh`, `deploy/envs/prod/values.yaml`. Chart: a `pokeproxy.image` helper emitting `repository@digest` when a digest is set and falling back to `repository:tag` otherwise, applied to all four image references; `digest: ""` added to the three first-party image blocks. CI: a `build-e2e` job and a prod `chart-lint` pass.

**A gap in the plan, found by checking GHCR rather than re-reading the plan: CI never built the e2e image.** Step 3's plan said it would; it wasn't implemented. `gh api user/packages/container/pokeproxy-e2e/versions` returns **404 package not found**, against 403-insufficient-scope for the two packages that do exist — the distinct status codes are what make this conclusive rather than inferred. The prod cluster pulls from GHCR with `e2e.enabled: true`, so 4b could not work without it. New `build-e2e` job, `needs: [build-pokeproxy]`, passing `BASE_IMAGE=ghcr.io/idanbro/pokeproxy@<digest>` — the digest, not the tag, so the derived image is pinned to the exact base that job just pushed.

**A second real finding, from watching the cluster rather than reading the values file: `applicationSet.enabled: false` was a silent no-op.** The ApplicationSet controller pod was running despite the setting. Argo CD chart 10.4.0 exposes `dex.enabled` and `notifications.enabled` but the `applicationSet` block has **only `replicas`** — no `enabled` key — and Helm accepts unknown values without complaint. Changed to `applicationSet.replicas: 0`; re-upgraded and confirmed the Deployment reports `0/0`. The general lesson is the one that matters: an unrecognised Helm value fails silently, so a disable-flag is only real once the thing is observed absent.

**The plan's retracted `valueFiles` claim, now confirmed empirically rather than by reasoning.** With the Application pointed at `main` (which does not yet carry `deploy/envs/prod/values.yaml`), Argo CD's error is `open <cached source>/deploy/envs/prod/values.yaml: no such file or directory` — a *file-not-found*, not a path-outside-repository rejection. So `../../envs/prod/values.yaml` escaping the Application's `path` is accepted; the enforced boundary is the repository root, exactly as the corrected plan says.

**Also worth recording: `health=Healthy` while `sync=Unknown`.** An Application with zero synced resources reports Healthy, because nothing in it is unhealthy. Health alone is a useless gate; `bootstrap-prod.sh` requires `Synced` **and** `Healthy`, and correctly refused to pass through ~40 polls over 600s of steady `ComparisonError`.

**Verified live so far (prod cluster):** cluster created on 8081 alongside dev with a distinct context; namespace + PSA labels applied; `seal-hmac.sh --env prod` generated `.secrets/sealing-key-prod.yaml` (distinct from local) and sealed a prod value; Argo CD 10.4.0 installed with server/repo-server/redis/application-controller all 1/1; prod chart renders 24 resources with `ghcr.io/idanbro/pokeproxy@sha256:b04f7238…` — digest pinning confirmed in the rendered output, tag fallback confirmed for `redis:7-alpine`.

**Everything in that list is now verified** — see "Step 4b verified live" above. The `e2e.image.tag` dangling reference was resolved by re-seeding all three tag/digest triples from CI run 32640298921 at `449c505`.

**Step 4a detail — env values moved out of the chart, N7 closed.** `values-local.yaml` → `deploy/envs/local/values.yaml` (git mv); `values-prod.yaml` **deleted** rather than moved — it described an environment that did not exist and could not deploy (S4). `scripts/seal-hmac.sh` gains `--env {local,prod}`, which selects the values file (`deploy/envs/$ENV/values.yaml`), the sealing key (`.secrets/sealing-key-$ENV.yaml`) and the cluster (`pokeproxy` / `pokeproxy-prod`). `deploy.sh` and `ci.yml` chart-lint each change one path.

**N7 fixed** — the wholesale `cat > "$VALUES_LOCAL"` is replaced by `write_encrypted_value()`: `sed` targeted replacement of the `encryptedValue:` line when the key is present, append when it isn't, then a `grep -qF` assertion that the value actually landed (refuses to continue otherwise). **Deviation from the plan, which said yq:** `apt-cache policy yq` is empty on this box (Ubuntu 22.04; yq entered Ubuntu at 23.04), so using it means a GitHub-release binary and a `require_command yq` that Part 5's clean-machine bootstrap can't satisfy from a package manager. `sed` is safe here for a specific reason worth stating: base64's alphabet (`A-Za-z0-9+/=`) contains none of `|`, `&` or `\`, so neither the delimiter nor the replacement text can be misparsed. CI's step-5 promote job will use yq, since `ubuntu-latest` ships it.

**A real bug in my own change, found by the test matrix rather than by review.** The already-sealed guard tested `! grep -q "encryptedValue: CHANGEME"` — so a values file with **no `encryptedValue` key at all** contains no "CHANGEME", was read as already-sealed, and was skipped, leaving the release with no HMAC secret. Inert before (local values always carried the key); live in 4b, where `deploy/envs/prod/values.yaml` is created fresh and hits exactly that path whenever the prod sealing key is reused. Replaced with `already_sealed()`, which tests for a **present, non-empty, non-CHANGEME** value.

| Guard case | Expected | Result |
|---|---|---|
| Existing key, other content in file | re-seals, everything else survives | pass — sentinel tag *and* digest intact |
| No `hmac` key at all | appends without loss | pass |
| Already sealed, key unchanged | file untouched | pass — sha256 identical |
| `encryptedValue:` present but **empty** | treats as unsealed, re-seals | pass (this is the bug above) |
| `encryptedValue: CHANGEME` | re-seals | pass |
| `--env staging` | rejected | pass |

**Verification, live against the dev cluster:** render before vs after the move **byte-identical**; `helm lint --strict` clean; `deploy.sh` green — revision 11, 4/4 pods, E2E post-upgrade hook passed (`--atomic` did not roll back), `/received` grew by exactly 1. `.secrets/sealing-key.yaml` was renamed to `sealing-key-local.yaml` (sha256 verified identical before/after) and the in-cluster decrypted `pokeproxy-hmac` still reads `dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg==` byte-for-byte — so the rename did not cost decryptability.

**A process note worth recording:** a `\1` sed backreference written through a Python heredoc landed in the file as literal byte `0x01`, silently breaking the guard until I dumped the line with `cat -A`. The same class of mangling later collapsed a `ci.yml` line-continuation block. Both caught by diff review, not by the tests.

**Step 5 detail — CI promote job.** New `promote` job in `ci.yml`, `needs: [build-pokeproxy, build-mock-downstream, build-e2e]`, `if: github.ref == 'refs/heads/main'` — so a pull request never writes to the repo, only a push to `main` does. `permissions: contents: write`, `environment: production` for the deployment audit trail, job-level `concurrency: {group: promote, cancel-in-progress: false}` so two promotes can't interleave their commits. Writes six fields into `deploy/envs/prod/values.yaml` with `yq` (preinstalled on `ubuntu-latest` — unlike the local box, see step 4a), using `strenv()` rather than string-interpolating digests into the expression, and `.components["mock-downstream"]` rather than dot access, since yq v4 parses a bare hyphen as subtraction. Commits as `github-actions[bot]`, subject `chore(deploy): promote <sha>`, digests in the body; `git pull --rebase` then `git push origin HEAD:main`.

**Branch protection reality check before writing this, not assumed from the plan:** `gh api repos/.../branches/main/protection` shows `required_status_checks` **and** `required_pull_request_reviews` both absent — direct pushes to `main` are unrestricted today. The promote job needs no bypass yet; D11's second half stays open, and the bypass this job will need the moment those checks land is documented in `deploy/README.md` rather than silently deferred again.

**Idempotency built in, not assumed:** if the six fields already match (a docs-only commit still runs the full build/scan/sign chain), `git diff --cached --quiet` short-circuits the job before any commit.

**Dry-run tested locally against a real copy of `deploy/envs/prod/values.yaml`**, using a scratch `yq` binary (not installed system-wide) and fabricated sha/digests: all six fields updated correctly, hyphenated key resolved, other content untouched. One real, minor, side effect found this way and documented rather than left as a surprise: `yq -i` re-serializes the whole file, so blank lines between blocks are dropped on every real promote — no comments or keys lost, cosmetic only.

**Not yet verified live** — the job only fires on a push to `main`, and this branch hasn't merged yet. Live verification (the promote commit landing, digest match on the running pod, confirmation the bot commit doesn't retrigger CI, and the commit-to-serving measurement) happens with the PR #3 merge, next.

**Step 3 detail — E2E check.** `app/e2e/e2e_check.py` (new): plain Python, no pytest (image built `--no-dev`), reads `POKEPROXY_HMAC_KEY` from env exactly like `config.py`, sends a unique payload per run (`number` random 900000-999999, `name` `e2e-<uuid4>`), asserts matching→200+delivered-with-correct-reason, non-matching→200 `{}`+absent, corrupted signature→401. `app/Dockerfile.e2e` (new): 4-line derived image, `FROM ${BASE_IMAGE}`, switches to root to `COPY --chown=10001:10001` (base image's `/app` is root-owned) then back to 10001. `deploy/helm/pokeproxy/templates/e2e/job.yaml` (new): dual-annotated hook (`helm.sh/hook: post-install,post-upgrade` + `argocd.argoproj.io/hook: PostSync`), `backoffLimit: 0`, `activeDeadlineSeconds: 120`, `ttlSecondsAfterFinished: 3600`, full PSA-restricted securityContext, gated on `.Values.e2e.enabled` (default false — `scripts/deploy.sh` now builds/imports the e2e image and passes `--set e2e.enabled=true --set e2e.image.tag=$GIT_SHA`). 3 new NetworkPolicy rules (e2e↔ingress, e2e↔mock-downstream). New `chart-lint` CI job: `helm lint --strict` + `helm template | kubeconform -strict -ignore-missing-schemas`.

**Two real bugs found via live execution against the dev cluster (recreated from zero — it doesn't persist across sessions), not by reading the spec:**
1. **Hook-timing race.** Helm's `post-install` hook fires as soon as the Job resource is created — it does **not** wait for the release's own Deployments to become Ready. First real deploy: Job failed instantly with `ECONNREFUSED` (a Service with zero ready endpoints rejects rather than times out — confirmed K8s behavior). Fixed with `wait_until_reachable()` in the script: retries a throwaway POST against the proxy for up to 90s on `ConnectError` specifically, leaves everything else (wrong content, wrong status) failing immediately. This is also why Argo CD's PostSync hook needs no special handling — the same script covers both invokers.
2. **NetworkPolicy port mismatch.** `allow-e2e-egress-to-ingress` specified `port: 80` (Traefik's Service port) but kept failing with `ECONNREFUSED` even with pods fully Ready and the rule syntactically correct. Root-caused by isolating a debug pod bearing the e2e labels: DNS resolved correctly, a pod with **no** NetworkPolicy restrictions (different namespace) reached Traefik's ClusterIP:80 fine, but the same pod inside `pokeproxy` with the policy applied was refused — including when the policy used `podSelector: {}` (ruling out label-scoping as the cause). Isolated to the actual variable by testing port 8000 directly: **this cluster's NetworkPolicy enforcement matches the destination pod's real listening port, not the Service port a client dials** — Traefik's Service maps port 80 → named port `web` → container port **8000**. The chart's own pre-existing `allow-ingress-to-pokeproxy` rule already did this correctly (uses pokeproxy's real container port); my new rule just used the wrong number. Fixed, and exposed as `e2e.traefikContainerPort` in values.yaml (with a comment) so it can't silently drift if Traefik's chart changes its listening port. **Side finding, corrects the plan:** this cluster's NetworkPolicy controller actively rejects (`ECONNREFUSED`) rather than silently dropping (timeout) — the plan's step-3 verification table originally assumed a timeout; corrected in `docs/planning/part-03-cicd-gitops.md`.

**Full verification, all against the real (recreated) dev cluster:**

| Check | Result |
|---|---|
| `helm lint --strict` + `helm template \| kubeconform -strict -ignore-missing-schemas` | clean, both with e2e on and off; 24 resources, 22 valid, 0 invalid, 2 skipped (SealedSecret/Middleware CRDs) |
| Chart renders gated | 0 e2e resources when disabled (default); exactly 1 Job + 3 NetworkPolicy rules when enabled |
| `deploy.sh` run 3× back to back | all green, real captured Job logs each time: `{"result": "pass", "checks": ["proxy reachable", "signed matching payload forwarded (200)", "delivered to mock downstream with correct reason", "non-matching payload not forwarded", "corrupted signature rejected (401)"]}` |
| `/received` growth | exactly 1 new record per passing run (3 records after 3 runs), each with the correct name and reason |
| Downstream broken (`mock-downstream.enabled=false`) | Job failed loudly on the real error (`502 downstream error`), `helm --atomic` auto-rolled back; `helm history` shows the failed revision (8) and the rollback revision (9, "Rollback to 7") |
| NetworkPolicy rule deleted (`allow-e2e-egress-to-mock-downstream`) | Job failed on `ECONNREFUSED` within ~20s — proves the rule is load-bearing, same A/B rigor as Part 2 step 8 |
| `chart-lint` CI job on real Actions | green, run [32628316380](https://github.com/IdanBro/PokeProxy/actions/runs/32628316380) |

**One CI fix needed after pushing:** `ruff` flagged `T201` (`print` found) on the script's three deliberate stdout summary lines — the same pattern already exempted for `scripts/**` (`load_generator.py` prints too). Extended the exemption to `e2e/**` in `pyproject.toml`. Re-verified: `ruff` clean, 106/106 tests, all 5 CI jobs green.

**Step 2 detail — build, scan, sign, push.** Two jobs (`build-pokeproxy`, `build-mock-downstream`) added to `ci.yml`, `needs: [lint, test]`: buildx → GHCR at `ghcr.io/idanbro/{pokeproxy,mock-downstream}:<short-sha>`, `sbom: true`, `provenance: mode=max`, Trivy gating HIGH/CRITICAL (`ignore-unfixed`), cosign keyless sign via OIDC. Actions pinned by commit sha (resolved via GitHub API): `docker/setup-buildx-action`, `docker/login-action`, `docker/build-push-action`, `aquasecurity/trivy-action`, `sigstore/cosign-installer`.

**Trivy's first real run found genuine HIGH CVEs, not a config bug** — proving the gate does what it's for. `setuptools` CVE-2025-47273 in both images, traced via the SBOM's `sourceInfo` to the base image's **unused system `pip`** (vendors an old setuptools internally; confirmed absent from the actual app venv even as root). `starlette` CVE-2026-48818 + CVE-2026-54283 in pokeproxy only — a stale `uv.lock` entry; `fastapi==0.135.1` only floors `starlette>=0.46.0`, so `uv lock --upgrade-package starlette` (→ 1.6.0) resolved clean against the existing fastapi pin. Fixed: both Dockerfiles now `rm -rf` system pip from the runtime stage; lockfile bumped. Verified: pip absent from both rebuilt images, both still `import` their app code cleanly, `ruff` clean, **106/106 tests pass** (one new harmless `starlette.testclient`→`httpx2` deprecation warning, test-only, not acted on). Re-run clean on real Actions.

**A second real bug, also found only by verifying against the pushed artifact, not by reading the workflow:** the `revision` label on the first successful push (`7dfc9070...`) didn't match the commit it was built from. Root cause: on `pull_request` events `github.sha` is GitHub's synthetic PR-merge commit, not the branch HEAD. Fixed by resolving `github.event.pull_request.head.sha` first, falling back to `github.sha` (unaffected on `push`/`workflow_dispatch`, which is the actual step-5 promote path). Re-verified: pulled the corrected image, label now reads `0b1e4c41171bd760eac527531f07f3874b590917` — exact match to `git rev-parse`, and independently confirmed via the SLSA provenance attestation's `build-arg:GIT_SHA`.

**Full step-2 verification, all against the real pushed artifacts:** anonymous `docker pull` by digest for both images (after flipping both GHCR packages to public — manual GitHub UI step); `cosign verify` against both, with the expected OIDC issuer (`https://token.actions.githubusercontent.com`) and repo identity regex, using a locally-installed `cosign v3.1.3` (not just trusting the CI step exited 0); `docker buildx imagetools inspect` confirming both SBOM (SPDX, via syft) and SLSA provenance attestations are attached; a follow-up run showing 5 `CACHED` buildx layers from the `type=gha` cache.

**Branch protection on `main` is enabled** (user, 2026-08-23) but **not yet requiring the `Lint`/`Test` status checks** — checked via `gh api repos/.../branches/main/protection`, `required_status_checks` is null. Force-push and branch deletion are blocked; the check-requirement half of D11 is still open.

**Step 1 detail.** `.github/workflows/ci.yml` — two parallel jobs (`ruff check .`, `pytest -q`), triggers `pull_request` / `push:main` / `workflow_dispatch`, `astral-sh/setup-uv@20cfd1bf...` (v10.0.1) pinned to `version: 0.12.5` to match `app/Dockerfile`, `actions/checkout@3d3c42e5...` (v7.0.1) — both actions pinned by commit sha, resolved from the GitHub API rather than guessed. `uv sync --locked` (not the Dockerfile's `--frozen`) so a stale `uv.lock` fails CI instead of silently building against a lock that no longer matches `pyproject.toml`. `.github/dependabot.yml` — `github-actions` (root, weekly) + `docker` (`/app`, weekly — covers both `Dockerfile` and `Dockerfile.mock`).

**Verified live, not just rendered:** local WSL run of the exact CI commands (`uv sync --locked`, `uv run ruff check .`, `uv run pytest -q`) — clean, 106 passed — before ever pushing. Then on real Actions via draft PR #3: run [32621454866](https://github.com/IdanBro/PokeProxy/actions/runs/32621454866) green (both jobs, 106 passed, ruff clean) but flagged an annotation — `Failed to save: Unable to reserve cache with key ...`, both jobs racing on one shared uv cache key. Fixed with a `cache-suffix` per job (`lint`/`test`); re-run [32621490886](https://github.com/IdanBro/PokeProxy/actions/runs/32621490886) green with no annotation. Then the scratch-failure check: a deliberately failing test (`assert False`) pushed and reverted — run [32621529244](https://github.com/IdanBro/PokeProxy/actions/runs/32621529244) went red on **both** jobs (`pytest` on the failing assertion, `ruff` independently on bugbear B011 for `assert False`), proving the gates are real rather than decorative. Revert run [32621554095](https://github.com/IdanBro/PokeProxy/actions/runs/32621554095) confirmed green again with an explicit `Cache hit` / `Cache restored successfully` log line on both jobs — the warm-cache claim, measured not assumed.

**Not yet done:** branch protection on `main` requiring these checks (D11) — operator action in the GitHub UI, mine to request, not something I can do from here.

**Part 2 — Infrastructure & Deployment: complete and re-audited at HEAD `cd72953` on 2026-08-23. No blockers. S5 and S6 fixed same-day and verified live; S4 plus eight NICE TO HAVE remain open.** PokeProxy, Redis, and mock-downstream run in a real k3d cluster, reachable through a real ingress, with secrets sealed, network policy enforced, rollouts proven safe under live load, and every fixed issue documented with a write-up. Design and decisions are recorded in `docs/planning/part-02-infrastructure-deployment.md`; the Part 2 section below carries the day-by-day narrative and measured results, and that file's two audit sections — **"Part 2 completion audit — 2026-08-23"** and **"Part 2 re-audit at HEAD `cd72953` — 2026-08-23"** — carry the full evidence. Part 1 remains functionally complete (detail retained below).

**Part 2 audit result (2026-08-23) — deployed behavior verified at HEAD `721b8fc`, not just manifest syntax.** Rebuilt both images at the HEAD sha, re-imported, `helm upgrade --install --atomic` → revision 8, 4/4 pods, 0 restarts. What was proven by execution: a signed request through the **real ingress** forwards and lands in mock-downstream; a repeated payload is deduped (each unique payload appears exactly once in `/received`); a 2 MiB body is rejected **413 at the ingress**; `/health`, `/ready`, `/stats` are **404** through the ingress; scaling Redis to zero leaves pods `Ready` with **zero 5xx** and 0 restarts; a `rollout restart` under **20 rps of all-unique payloads** produced **1113/1113 × 200, 0 errors**; a `privileged`/`runAsUser: 0` pod is **rejected by PSA**; an unlabeled pod is refused by Redis, mock-downstream *and* pokeproxy; the container runs as `uid=10001` on a read-only `/app`; `pytest -q` **106 passed** (was recorded as 101 — that count predated the step-2 entrypoint tests), `ruff` clean.

**Both BLOCKERs fixed 2026-08-23, same session as the audit, both re-verified live:**
1. **B1 — the committed sealed HMAC ciphertext only decrypted on the machine that sealed it.** `scripts/seal-hmac.sh` skipped re-sealing whenever `values-local.yaml` already held a value, so a fresh clone — which has no `.secrets/sealing-key.yaml` and therefore mints a brand-new sealing key — kept ciphertext encrypted against the old key. Proven live: sealing with a foreign key and watching the controller reject it (`no key could decrypt secret`); the controller's active key fingerprint matched the gitignored file exactly. **Fix:** the script now tracks whether it just generated a new key and force-reseals when it did, regardless of the file's existing contents. **Re-verified by simulating the exact failure**: deleted the local sealing key, re-ran the script, got a fresh key + fresh ciphertext, redeployed (`helm --atomic` → revision 9, 4/4 pods, 0 restarts), and confirmed the decrypted `Secret` held the correct dev key byte-for-byte. A second run with the key unchanged correctly left the file untouched (idempotency intact). Original key/ciphertext restored afterward (revision 10, 0 restarts). Write-up: `docs/issues/017-sealed-secret-key-portability.md`.
2. **B2 — the namespace and its `pod-security.kubernetes.io/*: restricted` labels existed only as prose**, never as a file. **Fix:** new `deploy/k8s/namespace.yaml`, the exact manifest step 6's hand-run command already produced. **Re-verified live**: applying it against the already-existing namespace was a no-op (`namespace/pokeproxy configured`, labels byte-identical); applying a renamed copy against a namespace that had never existed created it correctly with full PSA enforcement. Write-up: `docs/issues/018-namespace-not-tracked.md`.

**S1–S3 fixed 2026-08-23, same day as the audit, all re-verified live:**
1. **S1 — `components.{pokeproxy,redis}.enabled` were declared but never read.** Same bug class as the mock-downstream fix in `721b8fc`, applied to the other two components: `pokeproxy/{deployment,service}.yaml` and `redis/{deployment,service}.yaml` now gate on `$spec.enabled`, matching the existing `serviceaccount.yaml` pattern. **Re-verified**: `helm template --set components.redis.enabled=false` now renders zero Redis resources (was: an orphaned Deployment with no ServiceAccount, `Forbidden` on pod creation).
2. **S2 — `enableServiceLinks` left at Kubernetes' default-true.** Added `enableServiceLinks: false` to all three pod specs. **Re-verified live**: redeployed (revision 11, 4/4 pods, 0 restarts) and confirmed the mock-downstream pod — previously showing `POKEPROXY_PORT=tcp://10.43.93.39:8000` injected by the Service-links mechanism — now has zero `POKEPROXY_*` env vars at all.
3. **S3 — no runnable deploy procedure anywhere in the tree.** New `deploy/README.md`: cluster creation, image build/import, namespace, secret sealing, deploy, verify, teardown — every command in it was re-run against the live cluster while writing it, not just described.

**New (2026-08-23) — `scripts/deploy.sh` turns the README's steps into one runnable, idempotent script.** Same six steps, scriptable end-to-end: reuses an existing k3d cluster instead of failing on it, builds/imports at the current `git rev-parse --short HEAD`, applies the namespace, runs `seal-hmac.sh`, `helm upgrade --install --atomic`, then verifies with `kubectl get pods` and a signed-request-shaped probe. **Running it live surfaced a real bug in the README itself**: the documented verify command (`curl -i http://localhost:8080/stream`) sends a bare GET, and `/stream` is POST-only — that returns **405**, not the 401 the README claimed. Both the script and `deploy/README.md` now use `curl -X POST`, and the script asserts on the 401 explicitly (exits non-zero otherwise). Re-verified after the fix: two consecutive full runs against the live cluster — first at revision 12, second at revision 13, both `4/4 pods Ready, 0 restarts`, both `401` on the probe, second run's every step (namespace apply, key reuse, ciphertext reuse) confirmed idempotent-as-designed. Full app E2E and `pytest -q` unaffected.
Full end-to-end sanity after all three: signed request through the real ingress still `200`, dedup still works, `pytest -q` **106 passed**, `ruff` clean — no regressions from the chart changes.

**Second audit (re-audit at HEAD `cd72953`, 2026-08-23) — no blockers; the from-zero path was executed for the first time.** The cluster had drifted again (running `cbb7911` images while HEAD was `cd72953` — the last commit's two `deploy.sh` runs predated the commit itself), and `deploy.sh` had only ever taken its cluster-reuse branch. So both branches were run. **Reuse branch:** 2m08s, revision 14, 4/4 pods, 0 restarts. **From zero:** `k3d cluster delete pokeproxy` (7.6s) then one `bash scripts/deploy.sh` — **4m39s to a fully running stack**, revision 1, 4/4 Ready, 0 restarts, ingress probe 401, working tree clean before and after (`values-local.yaml` sha256 `ad34639c…` unchanged). That run also closed the last caveat on B1: **the committed ciphertext decrypts on a cluster that never existed before**, because the sealing key is re-pinned into a freshly installed controller. Behavior re-proven against both clusters: **11/11 E2E checks through the real ingress** (200 · dedup · non-match · 401 · 401 · 400 · request-id echo · 413 · 3× 404), mock-downstream `/received` showing **5 records / 5 unique / 0 duplicates**, **Redis outage → 20/20 × 200, zero 5xx, pods stayed Ready**, **rollout restart under ~20 rps → 642/642 × 200, 0 errors**, PSA rejecting a privileged pod, NetworkPolicy denying all three services **by ClusterIP** (retested by IP after finding busybox `nslookup` exits non-zero on search-domain NXDOMAINs even when resolution succeeds — the DNS exception does work), `pytest -q` **106 passed**, `ruff` clean. Prerequisite failure is loud: a stripped `PATH` gives `Missing required command: kubectl`, exit 1.

**Three SHOULD FIX found by the re-audit; S5 and S6 fixed same-day, S4 still open** (full evidence in the planning doc's "Part 2 re-audit" section):
- **S5 — `scripts/deploy.sh` never pinned a kube context, so it could deploy into the wrong cluster. FIXED.** `k3d cluster list pokeproxy` is context-independent (measured exit 0 under both `k3d-pokeproxy` and `docker-desktop`), so on the reuse path the script skipped the only step that would have switched context and then ran `kubectl apply` and `helm upgrade` against whatever context was current. `scripts/seal-hmac.sh` was worse — it applies an RSA private key into `kube-system` first. **Fix:** a `KUBE_CONTEXT` variable defaulting to `k3d-$CLUSTER_NAME` in both scripts, threaded onto every cluster-touching call (`--context` / `--kube-context`, including `kubeseal --raw`), plus an existence check that exits 1 naming the context. Explicit flags rather than `kubectl config use-context`, so the scripts stop depending on ambient shell state instead of overwriting it. **Proven live**: reproduced the old misfire (bare `kubectl apply` under `docker-desktop` gives `dial tcp 127.0.0.1:6443: connection refused`), then ran the fixed `deploy.sh` with `current-context = docker-desktop` and it **deployed to `k3d-pokeproxy` anyway** (revision 2, 4/4 Ready, 401 probe, 1m15s), leaving the operator's context untouched. A bogus context exits 1 before any image build. Write-up: `docs/issues/019-deploy-scripts-unpinned-kube-context.md`.
- **S6 — S1's `enabled` fix was incomplete. FIXED.** With `components.pokeproxy.enabled=false` the chart still rendered both ConfigMaps, the SealedSecret, the Ingress and the Traefik Middleware; the Ingress routed `/stream` to a Service that was never created, so Traefik answered 503 instead of the flag meaning "not deployed." **Fix:** all five gated on `components.pokeproxy.enabled`. The four pokeproxy NetworkPolicies were deliberately left ungated — provably inert without pokeproxy (selectors match nothing, or allow ingress from nothing), and gating them would mix a per-component flag into a file switched by `networkPolicy.enabled`. **The naive placement was a trap:** putting the `if` after the `{{- $var := … -}}` assignments in `configmap-env.yaml` emitted a leading newline, moved `checksum/config-env`, and would have rolled every pokeproxy pod for a whitespace edit — caught by diffing renders against `git show HEAD:` copies. Moving the `if` above the assignments made both the `values-local` and `values-prod` renders **byte-identical to HEAD**, and the live redeploy confirmed it: checksums unchanged, **no pod rolled**. Disabled render went 17 resources to 12. Write-up: `docs/issues/020-pokeproxy-enabled-flag-incomplete.md`.
- **S4 — carried, and worse than recorded.** Beyond the rules pointing at the disabled mock Service, `allow-pokeproxy-egress-to-dependencies` only permits egress to in-namespace redis/mock pods, so `default-deny-all` blocks any real external downstream. `values-prod.yaml` is undeployable on two counts; only one was written down.

**Eight NICE TO HAVE open** — N1–N6 unchanged, plus N7 (`seal-hmac.sh:96` rewrites `values-local.yaml` wholesale, discarding any other local override) and N8 (`deploy/README.md:44` overstates when re-sealing happens — a fresh cluster with the key on disk correctly does not re-seal).

**Part 1 (complete) — both final-audit SHOULD FIX findings closed.** R1 (retry attempt-timeout, `docs/issues/012-retry-attempt-timeout.md`) and D1 (consolidated known-gaps write-up, `docs/issues/000-known-gaps.md`) are fixed. 15 issue IDs now fixed across 12 changes, 13 write-ups, **101 tests** passing from `app/` and the repo root, `ruff` clean. R2, R3, R4 (nice to have, from the same audit) and the pre-existing NICE TO HAVE backlog (L1, L2, L5, M6, H6) remain open, tracked in `docs/issues/000-known-gaps.md`. Part 1 is functionally complete; Part 2 not started.

**Completed:**
- Read-only review of `app/`: source, config, tests, mock service, load generator.
- Prioritized Part 1 issue inventory (below).
- `docs/planning/part-01-production-hardening.md`.
- **Wave 0 (L7) — toolchain works.** `app/.venv` (Python 3.13) plus `uv 0.12.5`, both **inside WSL Ubuntu**, not on Windows. All verification runs via `wsl.exe`. Windows Python is still 3.11 and `uv` is not on the Windows PATH.
- **Wave 1 (C1) — HMAC key name and validation.** Write-up: `docs/issues/001-hmac-key-configuration.md`.
- **Wave 2 (C5) — structured JSON logging, `X-Request-ID` correlation, outcome seam.** Write-up: `docs/issues/002-structured-logging.md`.
- **Wave 3, part 1 (C2) — bounded forward retry, shared client, `.env`-configurable cap/deadline.** Write-up: `docs/issues/003-unbounded-forward-retry.md`.
- **Wave 3, part 2 (C3) — cache lookup is a single `GET`, not a keyspace scan.** Write-up: `docs/issues/004-cache-keyspace-scan.md`.
- **Wave 3, part 3 (C4) — Redis calls guarded, client-level timeouts.** Write-up: `docs/issues/005-unguarded-redis-calls.md`.
- **Wave 3, part 4 (H1) — rules loaded and validated once at startup, not per request.** Write-up: `docs/issues/006-rules-reloaded-per-request.md`.
- **Wave 4, part 1 (M1 + H7) — real `/ready` endpoint split from `/health`, flipped false at the start of shutdown.** Write-up: `docs/issues/007-liveness-readiness-split.md`.
- **Wave 4, part 2 (H2 + H3) — header hygiene in both directions.** Request headers to downstream now go through an allowlist (currently empty); response headers to the client have hop-by-hop headers stripped. Write-up: `docs/issues/008-header-hygiene.md`.
- **Wave 4, part 3 (H4 + H5) — outcome accounting fixed, unbounded response-time storage removed.** `EndpointStats.record_request(is_error)` makes request/error counting atomic, so `error_rate` can no longer read 0.0 during a total outage. Rejections and `no_rule_matched` now count via a new outcome-keyed `StatsRegistry.record_outcome`. `_response_times`/`percentile()` deleted entirely (not bounded) — percentiles are Part 4's job (Prometheus histograms), `/stats` keeps only `avg_response_time` (already O(1) memory). Write-up: `docs/issues/009-outcome-accounting-and-unbounded-stats.md`.
- **Wave 3, part 5 (M4) — cache becomes a real dedup layer.** A hit now replays the actual cached downstream response (status, filtered headers, content) and skips decode/routing/forward entirely — transparent to the client. Only `forwarded` outcomes get cached, never `downstream_timeout`/`downstream_error`. New `duplicate_suppressed` outcome, `CACHE_TTL_SECONDS` now `.env`-configurable. Write-up: `docs/issues/010-cache-becomes-a-dedup-layer.md`.
- **Wave 5 (M7-CWD) — test suite no longer depends on the invoking shell's working directory.** New `app/tests/conftest.py` pins CWD to `app/` via `pytest_configure()`, computed from the test files' own location. Verified from three different working directories (`app/`, repo root, `/tmp`) — 94/94 every time. Write-up: `docs/issues/011-test-suite-cwd-dependence.md`.

**Currently working on:**
- Nothing in flight. Part 2 is implemented, audited, and both BLOCKERs are fixed; stopped for review with **S1–S4 (should fix) open** — see the audit summary above and the full evidence in the planning doc. R2, R3 and the pre-existing NICE TO HAVE items (L1, L2, L5) stay open, tracked in `docs/issues/000-known-gaps.md`.
- **Local environment state:** k3d cluster `pokeproxy` running with `8080:80@loadbalancer` port-mapped to the host, full stack deployed via `scripts/deploy.sh` at image tag `cbb7911` (helm release **revision 13** — revisions 8/9 were the audit deploy and the B1 fresh-clone simulation, revision 10 restored the original secret, revision 11 the S1/S2 chart-gating redeploy, revisions 12/13 the two `deploy.sh` verification runs), healthy, 0 restarts, reachable at `http://localhost:8080/stream`. `deploy/k3d/cluster.yaml` carries that port mapping. `deploy/k8s/namespace.yaml` (new, committed) creates the namespace with its PSA labels before Helm runs. `kubectl`/`k3d`/`kubeseal` installed in WSL at `~/.local/bin`. `.secrets/sealing-key.yaml` (gitignored) holds the pinned sealing keypair, restored to its original value after the B1 verification; `deploy/helm/pokeproxy/values-local.yaml` (committed) holds the sealed HMAC ciphertext, also restored to its original value. `values-prod.yaml` exists but has never been deployed anywhere — no production cluster exists.

**New standing rule (2026-08-23), added to `CLAUDE.md`:** write self-explanatory code with no comments (SOLID where the code has real structure to benefit — not forced onto trivial code); when a change makes existing code or tests obsolete, remove them as part of that change instead of leaving dead weight, scoped to what the current change touches. Applied immediately in step 3 — see below.

**Repository state:** branch `feature/infra-and-deployment`. All 9 prior Part 2 steps committed and pushed individually; step 10's changes (issue write-ups 013–016, `values-prod.yaml`, the two Helm template bugs it surfaced) are staged in the working tree, not yet committed as of this entry.

**Environment facts measured 2026-08-22/23, not assumed:** Docker Desktop 27.3.1 (was not running at session start — a bootstrap prerequisite that must fail loudly). `kubectl` v1.30.5 on Windows. Docker Desktop's own VM: **7.62 GiB / 8 vCPU** (confirmed via `docker info` — identical to WSL Ubuntu's own `free -h`/`nproc`, since Docker Desktop's WSL2 backend shares that same resource pool, not a separate allocation) — the ceiling Part 4's monitoring stack has to fit under. In WSL: `helm` present; **`kubectl` v1.30.5 and `k3d` v5.9.0 now installed** at `~/.local/bin` (step 6), pinned from each project's own official release channel (`dl.k8s.io`, `k3d-io/k3d` GitHub Releases) rather than `curl | bash`.

**L3 — deliberately deferred, not missed (decided 2026-08-22).** Error responses (`{"error": "downstream error"}`, etc.) carry no `request_id`, unlike `main.py`'s own `internal_error` handler, which does — inconsistent, and exactly the "useful error messages" gap the assignment names. Root cause and fix were fully scoped in a pre-change review: inject `request.state.request_id` into the content dict at the 6 real error call sites already funneled through `proxy.py`'s `_outcome_response()` helper (built during H4) plus the 2 `JSONResponse` literals in `_forward_request`'s except blocks; `no_rule_matched`'s `{}` body stays untouched since it isn't an error. A related, separately-decidable question was also raised and left open: `rejected_signature_missing` and `rejected_signature_invalid` currently return identical body text (`"invalid signature"`) despite being distinguishable outcomes in the logs.

**Why L3 deferred rather than fixed:** low severity (originally classified **Low**, the only Low-severity item in the SHOULD FIX set — H4/H5 were High, M2/M4/M7-CWD are Medium and each guard a real correctness/CI risk). The `X-Request-ID` response *header* already carries the correlation ID today, so this is a body-vs-header convenience gap, not a hard blocker. **Not forgotten — scoped and ready to implement in one pass through `proxy.py` whenever it's picked back up.**

**M2 — deliberately deferred, not missed (decided 2026-08-22).** `int(content_length)` malformed-header half was already disproved during C5 (uvicorn's own parser rejects it with a 400 before the handler runs). The real, still-live half: `await request.body()` fully buffers the request into memory *before* the `len(body) > MAX_BODY_SIZE` check ever runs — a client that omits or lies about `Content-Length` gets its entire payload buffered regardless of size, which is a genuine resource-exhaustion vector, not just a cosmetic gap. Fix was fully scoped in a pre-change review: read the body incrementally via `request.stream()`, counting bytes and aborting with 413 the moment the running total crosses `MAX_BODY_SIZE`, instead of reading to completion first. The existing `Content-Length` pre-check stays as a cheap first-pass shortcut for honest clients.

**Why M2 deferred rather than fixed:** user's explicit call, consistent with the H5 percentile-removal precedent — this class of protection is also achievable at the ingress/reverse-proxy layer in Part 2 (e.g., an Ingress `client_max_body_size`-equivalent), which is the more standard place production systems put it and rejects before the request even reaches this process. Recorded as a Part 2 addition below (defense-in-depth, not a replacement for the app-level fix, which stays scoped and ready if picked back up first).

**Next:**
0. **B1, B2, S1, S2, S3 are fixed** (2026-08-23) — see above and `docs/issues/017-sealed-secret-key-portability.md`, `docs/issues/018-namespace-not-tracked.md`. S4 (chart hygiene) can ride along with Part 3 or wait.
1. **Then Part 3 — CI/CD & GitOps:** a CI pipeline (lint/test/build), a CD side (GitOps preferred — Argo CD is the natural fit given the Helm chart already has one release per environment and `helm rollback`'s history), a post-deploy E2E gate, and a rollback story. Needs a fresh planning pass before implementation, per the CLAUDE.md working mode.
2. Two Part 2 constraints Part 3 must respect, both already load-bearing: the E2E must use a **unique payload per run** (M4's dedup replays cached responses for repeated payloads, which would make a re-run fail against a healthy deployment) and the image build/import step must always target the **exact sha it's about to deploy** — this session hit sha drift three separate times (steps 6, 7, 8) from images built in an earlier turn against an older commit.
3. Remaining NICE TO HAVE items (L1, L2, L5, R2, R3) stay tracked in `docs/issues/000-known-gaps.md`, unaffected by Part 2, available to pick up whenever.

**R1 — per-attempt HTTP timeout now less than the retry deadline (final audit fix).** `Settings.forward_attempt_timeout_seconds` (`FORWARD_ATTEMPT_TIMEOUT_SECONDS`, default 3.0) replaces the hardcoded `read=10.0`/`write=10.0` in the shared `httpx.AsyncClient`, which previously equalled `FORWARD_DEADLINE_SECONDS` and let one slow attempt consume the whole retry budget. New `Settings.model_validator` rejects `attempt_timeout >= deadline` at startup by name, so the exact bug can't be reintroduced via misconfiguration. `main.py` gained `_build_http_client(settings)` so the client construction is independently testable. Live re-probe against the same black-hole socket used in the audit: **3 of 3 attempts in 9.70s**, was 1 of 3 in 10.17s. New `test_retry_timeout.py` uses a real TCP server (a custom `httpx` transport bypasses timeout enforcement entirely) — surfaced that Python 3.13's `asyncio.Server.wait_closed()` also waits for already-accepted connections' handlers to finish, so the test's hung-server fixture cancels its handler tasks explicitly rather than waiting on them. 7 new tests (94 → 101): 5 in `test_config.py`, 2 in `test_retry_timeout.py`. `ruff check .` clean. Full detail in `docs/issues/012-retry-attempt-timeout.md`.

**D1 — consolidated known-gaps write-up (final audit fix).** New `docs/issues/000-known-gaps.md` covers the 11 found-but-unfixed issue IDs (H6, M2, L6, M5, L4, L5, M3, L1, L2, M6, plus the M2/L3 "deliberately deferred" reasoning already on record) in one document, grouped by disposition (deferred to a later Part / scoped-but-not-implemented / needs a protocol decision / low priority), plus R2/R3/R4 from the same audit pass. No code change — this closes the gap where `docs/issues/` only recorded what was fixed, not what was found and consciously left alone, which the assignment's deliverable 2 asks for either way.

**Part 1 completion audit (2026-08-22):** Full requirement-by-requirement pass against `README_HOME_ASSIGNMENT.md` Part 1 and this doc's own "Definition of done" (`docs/planning/part-01-production-hardening.md`). Verification run: `ruff check .` clean, `pytest -q` from `app/` — **73 passed**; from repo root — 25 fail (CWD-dependence, see Verified baselines).

*Satisfied:* reliability fixes for every issue actually fixed (C1-C5, H1-H3) each with a regression test and a `docs/issues/` write-up; structured logging; configuration hygiene for the HMAC key specifically; graceful shutdown (app-side, K8s-side correctly scoped to Part 2).

*Fixed since the audit:*
- **The outcome-accounting seam** — H4+H5 fixed 2026-08-22. See Decisions and changes below and `docs/issues/009-outcome-accounting-and-unbounded-stats.md`.
- **The dedup decision** — M4 fixed 2026-08-22, response-caching design (user's call, transparent to the client) rather than the synthetic-marker design originally proposed. See Decisions and changes below and `docs/issues/010-cache-becomes-a-dedup-layer.md`.
- **M7's CWD-dependence** — fixed 2026-08-22, `conftest.py`. See Decisions and changes below and `docs/issues/011-test-suite-cwd-dependence.md`.

*Not yet satisfied, despite being named or self-committed:* none remaining — every SHOULD FIX item is now either fixed or deliberately deferred with reasoning (see below).

*Deliberately deferred, with reasoning already on record — no gap:* K8s-side of graceful shutdown (H7, Backlog/Part 2), rules ConfigMap live-reload (H1 consequence, Backlog/Part 2), H6 config-assumes-localhost (fundamentally a Part 2 deployment-topology decision), M3 replay protection (documented-only, protocol change), M5 `/stats` auth (Backlog/Part 4), L4 unbounded label cardinality (Backlog/Part 4), L6 `mock_service` packaging (Backlog/Part 2), L5 ruff-in-CI (natural Part 3 fit), **L3 useful error messages** (reviewed, root-caused, fix scoped, deprioritized below Medium-severity work — see "L3 — deliberately deferred, not missed"), **M2 body size limit doesn't actually limit** (reviewed, root-caused, fix scoped, also achievable at the Part 2 ingress layer — see "M2 — deliberately deferred, not missed").

Full findings, severity, and reasoning: see the response given alongside this audit (not persisted verbatim here — token economy).

**Part 1 FINAL completion audit (2026-08-22, second pass).** Requirement-by-requirement re-read of `README_HOME_ASSIGNMENT.md` Part 1 against the tree at `e6e1e14`. No code changed.

*Verification actually run* (WSL Ubuntu, `app/.venv`, Python 3.13.15):

| Check | Result |
|---|---|
| `ruff check .` | All checks passed |
| `pytest -q` from `app/`, from the repo root, from `/tmp` | **94 passed** each time — M7-CWD's CWD-independence still holds |
| Service starts from `.env.example` verbatim (`cp .env.example .env`, no edits) | `startup complete` then serving on :8000; `.env` deleted afterwards, tree still clean |
| 10-case live probe through a running proxy + mock downstream, **Redis deliberately down** | 200 forwarded x3 · 200 `{}` no-rule-matched · 401 missing sig · 401 bad sig · 400 bad protobuf · 413 >1 MiB · caller-supplied `X-Request-ID` echoed verbatim |
| `/stats` after the probe | 3 endpoint requests, `error_rate` 0.0, and 5 populated outcome counters (`no_rule_matched`, `rejected_signature_missing`, `rejected_signature_invalid`, `rejected_protobuf`, `rejected_too_large`) — the H4 accounting seam works end-to-end |
| Redis-down degradation (C4) | 8 `WARNING cache lookup/write failed`, **zero 5xx** — degrades, does not fail. Duplicate payloads were re-forwarded (3 identical Charizards reached downstream), which is the documented at-least-once behaviour when dedup is unavailable |
| `SIGTERM` (H7) | `shutdown started` → `shutdown complete` → `Finished server process`, clean exit in **112 ms** |
| Secret hygiene | short / malformed / missing key each produce one `CRITICAL` naming `POKEPROXY_HMAC_KEY` plus the `openssl` command; the key value itself appears **0 times** in log output |
| Config fail-fast (C1, H1) | bad key, missing key and missing rules file each `SystemExit(1)` with a specific message naming the variable or the path |
| Retry behaviour under a slow downstream | **1 attempt of 3 in 10.17 s** — see R1 |

*New findings, not previously in the backlog:*

| ID | Sev | Finding | Evidence |
|----|-----|---------|----------|
| R1 | **Should fix** | The retry policy is inert against a slow downstream. The httpx per-attempt timeouts are hardcoded (`read=10.0`) and equal `FORWARD_DEADLINE_SECONDS` (10.0), so attempt 1 consumes the entire budget. Measured against a socket that accepts and never responds: **1 attempt of 3, 10.17 s**. Against a refused connection: 3 attempts, 0.48 s. `FORWARD_MAX_ATTEMPTS` therefore does nothing on the more common production failure mode (slow/hung, not refused), while `README.md` documents it as if it does. Failure is still bounded, so this is a wrong-knob bug, not an availability bug. Fix: make the per-attempt timeouts configurable and default `read` below the deadline (e.g. 3.0 against 10.0). | `main.py:70`, `proxy.py:89-101` |
| D1 | **Should fix** | Deliverable 2 is "for each issue you find, write it up". `docs/issues/` holds 11 write-ups covering the **14 fixed** issue IDs; the **11 found-but-unfixed** ones (M2, M3, M5, M6, H6, L1, L2, L3, L4, L5, L6) exist only as rows in this file's backlog table. A reviewer who opens `docs/issues/` sees no record that they were found at all. Cheapest fix: one consolidated `docs/issues/000-known-gaps.md` — problem / impact / proposed fix / why deferred, one short block each. | `docs/issues/` |
| R2 | Nice to have | Expected, *handled* Redis failures log a full traceback each (`exc_info=True`). Measured in the probe run: **388 of 418 log lines were traceback text** for 8 handled warnings — 93% of log volume, burying the 30 structured records that matter. At 10 rps with Redis down this becomes a log-pipeline cost problem. Fix: drop `exc_info` on these two warnings (the JSON `error` field already carries `ConnectionError: ...`), or emit it only at DEBUG. | `cache.py:20,50` |
| R3 | Nice to have | A downstream **5xx** is cached and replayed for the full `CACHE_TTL_SECONDS`. Issue 010 decided "cache any real downstream response" and justified it for 4xx business answers — sound for 4xx, weaker for a transient 503, which is then memoized and replayed to every duplicate for 5 minutes even after downstream recovers. One-line narrowing (`status_code < 500`) if picked up. | `proxy.py:131-142`, `docs/issues/010-cache-becomes-a-dedup-layer.md:19` |
| R4 | Nice to have | A config failure produces the intended single `CRITICAL` line **and then** uvicorn's own ~20-line `SystemExit: 1` lifespan traceback. The actionable line comes first and is correct, so this is noise rather than a defect. A container entrypoint that constructs `Settings` before handing off to uvicorn would remove it — natural Part 2 work. | verified above |

*Requirements satisfied, remaining gaps and the severity ranking are in the audit response given alongside this entry; not duplicated here (token economy).*

**C4 closed both deferred test-isolation reasons from C5.** `no_cache` in `test_logging.py` is no longer load-bearing for correctness (a Redis-down request now degrades instead of 500ing) — kept anyway for test speed and to keep unit tests off real network calls. Confirmed by a real end-to-end run with no mocking: unreachable Redis produced two `WARNING` log lines and a clean `502 downstream_error` in 727.8ms, not a crash.

**Verified baselines** (measured in WSL, not assumed):
- Test suite: **94 passed** from `app/` (5 → 16 after C1 → 28 after C5 → 38 after C2 → 44 after C3 → 48 after C4 → 56 after the C2/C4 config-naming follow-up → 62 after H1 → 67 after M1+H7 → 73 after H2+H3 → 87 after H4+H5 → 86 after removing percentile tracking → 94 after M4).
- **Tests are no longer CWD-dependent (fixed 2026-08-22, M7-CWD).** Before the fix: 35 of 94 failed from the repo root (had grown from 25 of 73 at the last audit, 3 of 48 before H1 — every new test that starts the app via `TestClient` inherited the failure). After: **94/94 pass identically from `app/`, the repo root, and `/tmp`.** `app/tests/conftest.py` pins CWD to `app/` in `pytest_configure()`.
- `ruff check .` passes across the project today, so L5 is a *missing gate*, not a backlog of violations.
- `aioredis.from_url` is **lazy**: the app starts fine with nothing listening on 6379. Input to C4 and M1.
- App module import alone costs ~3.2 s over the WSL `/mnt/c` filesystem. Re-measure startup timing inside the container in Part 2 rather than trusting numbers taken here.

**Important decisions so far:**
- Environment variables are the configuration interface. `.env` stays a local-dev convenience and is never the production mechanism.
- Redis is a best-effort cache and must **not** gate readiness. A Redis outage should cost latency, not availability.
- Fix the outcome-accounting *seam* in Part 1; defer the Prometheus backend to Part 4. Instrument once, not twice.
- Structured logging lands before the behavioural bug fixes, not after. Reasoning is in the planning doc.
- Standardize on `POKEPROXY_HMAC_KEY` — align the docs to the code rather than the reverse.
- **Minimum decoded HMAC key length is 16 bytes (128 bits), not 32.** This keeps C1 scoped to configuration: the existing 25-byte dev secret still passes, so `.env.example` and `scripts/load_generator.py` keep their current values and C1 does not spill into the load generator. L1 (a working secret committed to the repo) stays a standalone Wave 5 item.
- **M4 — a cache hit skips the downstream forward.** The cache becomes a deduplication / idempotency layer rather than a protobuf-decode cache, which is what "avoid re-processing previously seen payloads" actually implies and what makes the Redis dependency earn its place. **Fixed 2026-08-22** — see Decisions and changes and `docs/issues/010-cache-becomes-a-dedup-layer.md`. Sub-questions resolved below.
- **H1 — rules are loaded and validated once at startup.** No per-request disk read, no hot-reload. Invalid config is a startup failure, not a request-time 500. A rules change is a pod restart, which is an honest rollout story for Part 3.
- Order of work: make it start, make it visible, make it correct, make it survive Kubernetes, then hygiene.

**M4 sub-questions — resolved 2026-08-22** (were open questions blocking `cache.py`, now decided and implemented, see `docs/issues/010-cache-becomes-a-dedup-layer.md`):

- **What does a suppressed duplicate return to the client?** **Resolved: cache the actual downstream response and replay it byte-for-byte** — the user's call, and a better answer than either option originally on the table (a synthetic marker, or nothing). Fully transparent to the client; internally still a distinct `duplicate_suppressed` outcome.
- **Should the rules config hash be part of the cache key?** **Resolved: no** — accept and document the residual risk (a cached response can outlive a rules change for up to the TTL, same exposure that already existed from Redis persisting across restarts regardless of this decision).
- **Is 300s the right dedup window, and should it be configurable?** **Resolved: configurable** — `CACHE_TTL_SECONDS`, default 300.0, `.env`-configurable via the same shared validator as the other four operational settings.

**M2 decided and deferred** (was "still open from before" — see "M2 — deliberately deferred, not missed" above): app-level streaming enforcement is the scoped fix; an ingress-level cap is a Part 2 defense-in-depth addition (Backlog). Neither is implemented yet.

## Backlog / Later

Items discovered during the Part 1 review that intentionally belong to a later Part.

**Part 2 audit follow-ups (2026-08-23) — open, evidence in `docs/planning/part-02-infrastructure-deployment.md` "Part 2 completion audit"**

| ID | Sev | Item | Fix | Status |
|----|-----|------|-----|--------|
| B1 | Blocker | Committed sealed HMAC ciphertext only decrypted against the gitignored `.secrets/sealing-key.yaml`; `scripts/seal-hmac.sh` never re-sealed after minting a new key | Re-seal unconditionally when `generate_sealing_key()` actually ran this invocation | **Fixed** — `docs/issues/017-sealed-secret-key-portability.md` |
| B2 | Blocker | Namespace + `pod-security.kubernetes.io/*: restricted` labels existed only as prose; nothing in git created them | Committed `deploy/k8s/namespace.yaml`, applied before `helm upgrade` | **Fixed** — `docs/issues/018-namespace-not-tracked.md` |
| S1 | Should fix | `components.{pokeproxy,redis}.enabled` declared but never read — renders a Deployment whose ServiceAccount is never created | Gated `pokeproxy/{deployment,service}.yaml` and `redis/{deployment,service}.yaml` on `$spec.enabled`, matching the pattern already fixed for mock-downstream | **Fixed** |
| S2 | Should fix | `enableServiceLinks` default-true injects `POKEPROXY_PORT=tcp://…` into every pod; only env precedence saves pokeproxy | `enableServiceLinks: false` added to all three pod specs | **Fixed** |
| S3 | Should fix | No runnable deploy procedure and no top-level README (deliverable 8) | New `deploy/README.md` — every command in it re-run against the live cluster while writing it | **Fixed** |
| S4 | Should fix | `values-prod.yaml` renders rules pointing at the mock Service it disables — **and** `allow-pokeproxy-egress-to-dependencies` blocks egress to any real external downstream, so it is undeployable on two counts | Superseded: `deploy/envs/prod/values.yaml` describes an environment that actually exists (mock enabled, Traefik, GHCR digests); the external-downstream delta becomes prose in `deploy/README.md` | **Fixed** — `docs/issues/021-values-prod-undeployable.md` |
| S5 | Should fix | `scripts/deploy.sh` and `scripts/seal-hmac.sh` never pinned a kube context; `k3d cluster list` is context-independent (exit 0 under both contexts on this machine), so the reuse path could `kubectl apply` / `helm upgrade` — and apply an RSA private key to `kube-system` — against the wrong cluster | `KUBE_CONTEXT` defaulting to `k3d-$CLUSTER_NAME`, threaded onto every cluster-touching call, plus a loud existence check | **Fixed** — `docs/issues/019-deploy-scripts-unpinned-kube-context.md` |
| S6 | Should fix | S1's `enabled` fix was incomplete: with `components.pokeproxy.enabled=false` the chart still rendered both ConfigMaps, the SealedSecret, the Ingress and the Middleware. The Ingress backed onto a Service that was never created, so Traefik answered 503 | Gated all five on `components.pokeproxy.enabled`; NetworkPolicies deliberately left (provably inert). Renders byte-identical for both existing values files | **Fixed** — `docs/issues/020-pokeproxy-enabled-flag-incomplete.md` |
| N1–N6 | Nice to have | mock port hardcoded in the image · no PDB/anti-affinity · Redis unauthenticated · R2 traceback noise breaks one-JSON-per-line · tags not digest-pinned · `preStop.sleep` needs K8s ≥1.30 | see the audit section |
| N7 | Nice to have | `scripts/seal-hmac.sh:96` rewrites `values-local.yaml` wholesale with `cat >`, discarding anything else in the file | Merge, or narrow the write to the `hmac:` key — via yq | **Fixed** — `docs/issues/022-seal-hmac-wholesale-rewrite.md` |
| N8 | Nice to have | `deploy/README.md:44` says re-sealing happens on a "fresh clone or fresh cluster"; a fresh cluster with the key still on disk correctly does not re-seal | One-word doc fix | Open — found 2026-08-23 re-audit |

**Part 2 — Infrastructure & Deployment**
- Downstream URLs in `config/rules.json` are all `http://localhost:8001/pokemon`. These become per-environment ConfigMap values.
- **H1 consequence:** rules are read once at startup, so a rules ConfigMap change does nothing until the pods restart. The Deployment needs a pod-template checksum annotation over the rules ConfigMap (or an operator like Reloader) so a rules change actually rolls. Without it, a rules update looks applied in git and is silently inert in the cluster.
- `mock_service` is not in the wheel packaging (`packages = ["src/pokeproxy"]`) and binds `127.0.0.1`. Needs a deliberate containerization decision (L6).
- `preStop` hook and `terminationGracePeriodSeconds` are the manifest half of graceful shutdown (H7). The app-side hooks land in Part 1.
- Probe configuration encodes the "Redis does not gate readiness" decision (M1).
- **M2 — an ingress-level max-body-size cap** (e.g., an Ingress annotation) as a cheap, standard, defense-in-depth layer in front of the app-level fix — not a replacement for it (see M2 in the Prioritized backlog table).

**Part 3 — CI/CD & GitOps**
- `mock_service` keeps received payloads in an in-process list. If it ever runs more than one replica, the E2E check (post through the proxy, then read `/received`) can hit a different pod and see nothing. Pin it to a single replica or the E2E is flaky.
- `scripts/load_generator.py` is the natural seed for the E2E traffic generator. Its `sys.path.insert` hack and hardcoded default secret should be cleaned so CI can import it.
- **M4 is now implemented — the load generator will stop generating load as-is.** It picks from 12 fixed payloads (`POKEMON_DATA`, `random.choice` at `load_generator.py:92`), and protobuf serialization of identical field values is byte-identical, so there are exactly 12 distinct body hashes. Dedup now genuinely skips the forward on a repeat, so the first dozen requests exercise the downstream path and everything else for the next `CACHE_TTL_SECONDS` is suppressed. A 60s run at 10 rps would forward ~12 of 600 requests. Confirmed by test, not just predicted — `test_dedup.py`. The generator needs a varying field (nonce or timestamp) to stay useful for load testing, and Part 4 dashboards will read as near-zero forward rate until it has one.
- **M4 is now implemented — the post-deploy E2E must use a unique payload per run**, or flush the dedup key first. Re-running the same E2E payload inside the TTL produces no new downstream delivery — it replays the cached response instead — so the check fails on a healthy deployment. This is a correctness requirement for the Part 3 gate, not a nicety.
- Tests use relative fixture paths (`load_rules("config/rules.json")`) and only pass when CWD is `app/`. Fixed as part of M7 so CI is not CWD-dependent.
- **Sha-drift, observed three times in Part 2 (steps 6, 7, 8).** Any session gap where a commit lands between "build the image" and "deploy it" leaves the cluster running an image tagged for an older sha than `git rev-parse --short HEAD` now reports — `k3d image import` silently succeeds either way, so the mismatch only surfaces as `ImagePullBackOff` on the next deploy. Not a design flaw, just a manual-workflow gap that CI removes structurally: Part 3's pipeline must always build and import/push at the exact sha it's about to deploy, never reuse a cached image from an earlier step.
- **`values-prod.yaml`'s `components.pokeproxy.rules` has no real downstream URLs** (Part 2 step 10). The chart now supports an explicit `url:` per rule (verified — it overrides the auto-derived mock-downstream URL), but the values file itself doesn't set one, since there's no real production downstream to point at. Whoever stands up a real deployment from this chart needs to add real URLs before disabling `mock-downstream`.

**Opened by the Part 3 design (2026-08-23) — not blocking, logged so they aren't discovered later**
- **`mock_service.received_pokemon` grows forever.** In-process list, never trimmed. Harmless in dev; on a long-lived prod cluster every PostSync E2E run appends one more entry. Bound it, or have the E2E clear only its own entries.
- **The prod sealing key is gitignored like the dev one — but the consequence is not the same, and calling it "the same accepted trade-off" was wrong.** Corrected by the 2026-08-23 audit (F-2, now a **Blocker**): dev survives a regenerated key because Helm reads the re-sealed working tree, whereas prod cannot, because Argo CD reads the committed file from GitHub. A fresh clone therefore produces a prod cluster that cannot decrypt its own committed HMAC ciphertext, and `bootstrap-prod.sh` fails after 600s. Blocks Part 5's clean-machine bootstrap; needs a decision before Part 5.
- **Argo CD admin credentials need a handling decision** — the bootstrap prints the generated password; that is fine for a laptop stand-in and not fine for anything real.
- **Base images are still tag-pinned, not digest-pinned** (N5). Part 3 makes *our* images digest-pinned, which sharpens rather than solves this: a build re-run can still produce different bytes for the same commit because the base moved.
- **Deferred by decision, not oversight: an ephemeral k3d cluster inside the CI runner** as a pre-promotion gate. It is additive — the E2E is already a URL-parameterised Job — so adding it later is a workflow job, not a redesign.
- **Poisoned cache survives a rollback for up to `CACHE_TTL_SECONDS` (300).** Cached downstream responses are keyed by payload hash, so a bad version's wrong response replays after the rollback lands, for previously-seen payloads only. `redis-cli FLUSHALL` is the optional rollback step; documented in the Part 3 plan's rollback section.
- **GHCR has no retention policy.** Three images per commit, forever. Fine at this scale, wrong at any real one.
- **Argo CD is not itself GitOps-managed** — `bootstrap-prod.sh` installs it imperatively. App-of-apps is the standard answer to "who watches the watcher"; deliberately out of scope, recorded because it's the expected follow-up question.
- **The E2E Job mounts the real HMAC signing key.** The app validates against a single key, so a dedicated test credential needs a protocol change (M3). Accepted; blast radius is a Job in the namespace that already holds the Secret.
- **`ruff format` is not gated** (D8). If it's ever adopted, the sweep needs a `.git-blame-ignore-revs` entry.

**Part 4 — Observability**
- Replace `/stats` with Prometheus instrumentation, reusing the Part 1 outcome-accounting seam (H4, H5).
- **Which cluster gets the monitoring stack?** Part 3 introduces a second one. Argo CD and the GitOps flow live in `k3d-pokeproxy-prod`; `k3d-pokeproxy` is the dev throwaway. Decide when Part 4 begins.
- **M4 consequence, already satisfied by the seam:** `duplicate_suppressed` is already its own terminal outcome (implemented in M4, via the same `StatsRegistry.record_outcome()` seam H4 built) — carry the label through to Prometheus and make sure it's distinguishable on the dashboard from a genuine drop in inbound traffic.
- `StatsRegistry` keys on downstream URL via `setdefault` — bounded by the rules file today, but it is an unbounded-cardinality pattern that must not be carried into Prometheus labels (L4).
- Move operational endpoints (`/stats`, `/metrics`) onto a port the public Service does not expose (M5).
- If H1 ends up with hot-reload, expose a config hash so I can tell which pods have picked up a rules change.

**Documented, not implemented**
- M3 — no replay protection on signed payloads. The HMAC covers the body only, so a captured request is valid forever. The fix (signed timestamp inside the HMAC input, bounded acceptance window, nonce cache in Redis) is a protocol change I cannot make unilaterally. Write it up as a known gap.

---

## Part 1 — Code Review & Production Hardening

### Initial assessment

Three structural observations, ahead of any individual bug:

1. **It cannot start from its own documentation.** The code requires `POKEPROXY_HMAC_KEY`; the README and `.env.example` both say `POKEPROXY_SECRET`. Nobody has run this from a clean checkout recently. *(Fixed in C1 — the service now starts from `.env.example` verbatim.)*
2. **It is completely dark.** No `logging` import anywhere in `app/`. The only introspection is `/stats`, and `/stats` is wrong in precisely the way that hides an outage.
3. **The failure handling is inverted.** The dependency that should degrade gracefully (Redis) is a hard failure; the dependency that should fail fast (a dead downstream) retries forever. That is backwards, and it is the difference between a bad minute and a bad night.

### Prioritized backlog

Wave numbering matches the order of work in `docs/planning/part-01-production-hardening.md`.

| ID | Sev | Issue | Primary evidence | Wave | Status |
|----|-----|-------|------------------|------|--------|
| L7 | — | No `.venv`, `uv` not on PATH, local Python is 3.11 vs required 3.13. Nothing verified by execution yet. | environment | 0 | **Resolved** — venv + `uv 0.12.5` exist, but in **WSL**, not Windows. Verification runs via `wsl.exe`. |
| C1 | Critical | HMAC secret var name matches no docs, and the value has no meaningful validation. `changeme` (6 bytes), `""` (0 bytes) and `abcd efgh` (whitespace silently discarded) all started successfully. A base64 *padding* error did fail at startup, accidentally, as a bare `binascii.Error`. | `config.py:18,23-25`, `.env.example:1`, `README.md:52` | 1 | **Fixed** — `docs/issues/001-hmac-key-configuration.md` |
| C5 | Critical | No logging of any kind. Every failure path returns JSON and vanishes. No request or correlation ID. Measured: bad vs **missing** signature logged identically; a Redis-down 500 produced ~100 raw traceback lines. | no `logging` import in `app/` | 2 | **Fixed** — `docs/issues/002-structured-logging.md`. Included the C1 leftover (clean config-failure line). |
| C2 | Critical | `while True` retry with no cap and no deadline, a new `AsyncClient` per attempt never closed, `timeout=600.0` overriding the configured timeouts, and `app.state.http_client` left as dead code. Measured: 4 leaked clients in 3s against a refused connection, still retrying. | `proxy.py:54-66`, `main.py:24-27` | 3 | **Fixed** — `docs/issues/003-unbounded-forward-retry.md`. Cap and deadline are `.env`-configurable (`FORWARD_MAX_ATTEMPTS`, `FORWARD_DEADLINE_SECONDS` — corrected from an earlier, wrongly-documented `POKEPROXY_` prefix, see below). |
| C3 | Critical | `redis.keys("pokeproxy:pokemon:*")` on every request, then a Python-side linear scan to find the key a single `GET` would have returned. | `cache.py:18` | 3 | **Fixed** — `docs/issues/004-cache-keyspace-scan.md`. |
| C4 | Critical | Redis calls unguarded, no socket or connect timeouts. A Redis blip becomes a 500 on 100% of traffic; a hung Redis blocks indefinitely. Both halves reproduced: connection-refused and a raw TCP server that accepts and never responds. | `proxy.py:142,153`, `main.py:29` | 3 | **Fixed** — `docs/issues/005-unguarded-redis-calls.md`. |
| H1 | High | `rules.json` re-read, re-parsed and re-validated from disk on every request, synchronously, on the event loop. Config errors surface at request time instead of startup. | `proxy.py:207` (pre-fix), `rules.py:110-135` | 3 | **Fixed** — `docs/issues/006-rules-reloaded-per-request.md` |
| M1 | Medium | `/health` is a hardcoded string. No readiness concept. | `main.py:167-169` (pre-fix) | 4 | **Fixed** — `docs/issues/007-liveness-readiness-split.md` |
| H7 | High | No graceful shutdown and no readiness flip on SIGTERM. Every rollout drops in-flight requests. | `main.py:60-98` (pre-fix) | 4 | **Fixed (app-side half)** — `docs/issues/007-liveness-readiness-split.md`. K8s-side `preStop`/grace-period wiring is Part 2 |
| H2 | High | Downstream response headers copied verbatim to the client, including framing and hop-by-hop headers. | `proxy.py:132` (pre-fix) | 4 | **Fixed** — `docs/issues/008-header-hygiene.md` |
| H3 | High | Client headers forwarded downstream on a denylist basis. Denylists are always incomplete. | `proxy.py:34-45` (pre-fix) | 4 | **Fixed** — switched to an allowlist, `docs/issues/008-header-hygiene.md` |
| H4 | High | `request_count` only increments on success while `error_count` increments on failure, so `error_rate` reads 0.0 during a total outage. `bytes_received` is assigned rather than accumulated. Rejections and no-rule-matched are never counted. | `stats.py:29`, `proxy.py:87,95,101,162` (pre-fix) | 4 | **Fixed** — `docs/issues/009-outcome-accounting-and-unbounded-stats.md` |
| H5 | High | `_response_times` grows without bound and `bisect.insort` is O(n) per insert, so memory and CPU degrade with uptime. | `stats.py:15,19` (pre-fix) | 4 | **Fixed** — bounded to 1000 samples, `docs/issues/009-outcome-accounting-and-unbounded-stats.md` |
| H6 | High | Config assumes localhost, relative paths and a loopback bind. None of it survives a container. | `config/rules.json`, `config.py:15`, `mock_service/main.py:34` | 4 / P2 | Open |
| M2 | Medium | ~~`int(content_length)` unguarded, so a malformed header is a 500.~~ **First half disproved:** measured during C5 — uvicorn's httptools parser rejects `Content-Length: abc` with its own 400 before the handler runs, so `int()` never sees a non-digit string. Second half stands: body is fully buffered *before* the size check, so the 1 MiB limit does not actually limit anything — a genuine resource-exhaustion vector. | `proxy.py:173-183` (current) | 5 | **Deferred, deliberately — reviewed and root-caused 2026-08-22, fix fully scoped (stream + count via `request.stream()`, abort at 413 before finishing the read), not implemented. Also achievable at the Part 2 ingress layer as defense-in-depth — see "M2 — deliberately deferred, not missed" above.** |
| M6 | Medium | `POKEPROXY_PORT` is defined and documented but read by nothing. The real port comes from the uvicorn CLI. | `config.py:20`, `.env.example:3` | 5 | Open |
| M7 | Medium | ~~Five tests, all on decode/parse/match. Nothing covers `/stream`, HMAC, cache, Redis failure, downstream failure, headers or size limits.~~ **Disproven by the incremental regression suite** — each Wave fix added its own coverage; 94 tests span `/stream`, HMAC, cache hit/miss/failure (including a real cache **hit** end-to-end via `test_dedup.py`, added with M4), Redis failure, downstream timeout/error, headers (both directions), size limits, readiness, startup failure, and dedup. The one real remaining item — CWD-dependence — is **fixed**, see `docs/issues/011-test-suite-cwd-dependence.md`. | `tests/test_basic.py` (pre-fix framing) | 5 | **Fixed** |
| L1 | Low | A working HMAC secret is committed in `.env.example` and hardcoded as the load generator default. | `.env.example:1`, `load_generator.py:74` | 5 | Open |
| L2 | Low | Empty-name payloads rejected as "likely garbage input" — a heuristic wearing validation's clothes, with a misleading error message. | `config.py:83-84` | 5 | Open |
| L3 | Low | Error responses are opaque and uncorrelatable. `{"error": "downstream error"}` gives support nothing to search on. | `proxy.py:157,140,147` (current) | 5 | **Deferred, deliberately — reviewed and root-caused 2026-08-22, fix fully scoped (inject `request.state.request_id` at the 6 `_outcome_response` call sites + 2 `_forward_request` except blocks), not implemented. Prioritized M2/M4/M7-CWD instead — see "L3 — deliberately deferred, not missed" above.** |
| L5 | Low | `ruff` is configured with a good ruleset and nothing runs it. No type gate despite `# type: ignore` throughout. | `pyproject.toml` | 5 | Open |
| M4 | Medium | Cache costs a Redis round trip to save a microsecond-scale protobuf decode, and a hit still forwards downstream anyway. | `cache.py`, `proxy.py` (pre-fix) | 3 | **Fixed** — `docs/issues/010-cache-becomes-a-dedup-layer.md` |
| M3 | Medium | No replay protection. The HMAC covers the body only. | `proxy.py:36-38` | — | Deferred, document only |
| M5 | Medium | `/stats` is unauthenticated and leaks internal downstream URLs. | `main.py:47-50` | — | Deferred to Part 4 |
| L4 | Low | `setdefault` keyed by URL — an unbounded-cardinality pattern. | `stats.py:53` | — | Deferred to Part 4 |
| L6 | Low | `mock_service` is not in the wheel packaging and imports as a top-level module. | `pyproject.toml` | — | Deferred to Part 2 |

R1 and D1 were found in the final audit (2026-08-22) and are now **fixed** (`docs/issues/012-retry-attempt-timeout.md`, `docs/issues/000-known-gaps.md`) — see "Decisions and changes" above. R2, R3, R4 remain open, tracked in `docs/issues/000-known-gaps.md` rather than repeated here.

### Decisions and changes

**C1 — HMAC key configuration (Wave 1).** Standardized on `POKEPROXY_HMAC_KEY` and aligned `.env.example` and the README to it; rejected `AliasChoices` because there is nothing to migrate. Added a `field_validator` on `Settings` enforcing strict base64 and a 16-byte decoded minimum, with an error naming the variable and giving the `openssl` command. `hmac_key` and the validator share one `_decode_hmac_key()` helper.

Verified: 11 new tests (suite 5 → 16); the new module run against HEAD's code fails 9 of 11, confirming real regression cover; the documented Quick Start now reaches `Application startup complete` where it previously exited 3; `POKEPROXY_HMAC_KEY=changeme` exits 3 with the actionable message; `ruff check .` clean.

Deliberately out of scope: `scripts/load_generator.py` is untouched (its 25-byte default still passes the 16-byte floor — that was the reason for choosing 16). Full reasoning and residual risk in `docs/issues/001-hmac-key-configuration.md`.

**C2 — bounded forward retry (Wave 3).** `_forward_with_retry` now reuses the already-existing, already-correctly-configured `app.state.http_client` instead of leaking a new `AsyncClient` per attempt, and stops after a configurable `FORWARD_MAX_ATTEMPTS` (default 3) or `FORWARD_DEADLINE_SECONDS` (default 10.0), whichever comes first — both validated at startup like C1. Exhaustion re-raises the original exception type, so the existing `downstream_timeout`/`downstream_error` outcome handling from C5 applies with no new outcome value. `RetryPolicy` (a frozen dataclass) and a pure `_next_backoff_delay` function replace the old ad-hoc loop state.

The retry loop takes its clock and sleep function as injected dependencies (`clock: Callable[[], float] = time.monotonic`, `sleep: ... = asyncio.sleep`) rather than calling them directly — this came out of debugging a flaky deadline test that monkeypatched the global `time.monotonic` and starved asyncio's own internal scheduler, which reads the same function.

Verified: 10 new tests (28 → 38) — 7 isolated retry-loop tests plus the 3 `forwarded`/`downstream_timeout`/`downstream_error` outcome tests C5 had deferred, now unblocked; manual end-to-end check (downstream mocked, cache bypassed since Redis wasn't guarded yet) showed a bounded 502 in ~277ms where the old code would have retried forever; `ruff check .` clean. Full detail in `docs/issues/003-unbounded-forward-retry.md`.

**C3 — cache lookup is a single GET (Wave 3).** `get_cached_pokemon` replaced `KEYS "pokeproxy:pokemon:*"` plus a Python-side scan with one `redis.get(cache_key)` — same key format, same TTL, same return shape, no behavior change beyond dropping the O(keyspace) cost. No live Redis in this environment, so verification uses a minimal in-memory fake implementing the `get`/`set`/`keys` subset actually called; the regression test asserting `keys_calls == 0` fails against HEAD's code (`1 == 0`) and passes against the fix. 6 new tests (38 → 44); `ruff check .` clean. Full detail in `docs/issues/004-cache-keyspace-scan.md`.

**C4 — guarded Redis calls, client-level timeouts (Wave 3).** `get_cached_pokemon`/`cache_pokemon` each wrap their Redis call in `try`/`except RedisError`, logging a `WARNING` with the traceback and degrading to a miss / a no-op write instead of propagating. `aioredis.from_url` gains `socket_connect_timeout=2.0` and `socket_timeout=2.0`, so a hung (not just unreachable) Redis is also bounded. Guard lives inside `cache.py`, not at the `proxy.py` call sites — best-effort is a property of the cache abstraction, so callers shouldn't need to know Redis can fail.

Verified two distinct failure modes for real, not just against a fake: a refused connection (full request through `TestClient`, no mocking — two `WARNING` lines, then a clean `502` in 727.8ms) and a genuinely hung Redis (a raw TCP server that accepts and never responds — `TimeoutError` raised at the 2.0s socket timeout, caught, logged, process exits cleanly in 2.88s, not a hang). 4 new unit tests (44 → 48); the same 4 run against pre-fix `cache.py` all fail with an uncaught `ConnectionError`. `ruff check .` clean. Full detail in `docs/issues/005-unguarded-redis-calls.md`.

**Follow-up — Redis timeouts made `.env`-configurable, and a real naming bug fixed.** `REDIS_CONNECT_TIMEOUT_SECONDS`/`REDIS_SOCKET_TIMEOUT_SECONDS` (both default 2.0) replace the two hardcoded constants from the original C4 fix. While extending the pattern, found that C2's `.env.example`/`README.md`/validator messages documented `POKEPROXY_FORWARD_MAX_ATTEMPTS`/`POKEPROXY_FORWARD_DEADLINE_SECONDS` — but `Settings` has no `POKEPROXY_` prefix, so the *actual* working names were always `FORWARD_MAX_ATTEMPTS`/`FORWARD_DEADLINE_SECONDS` (matching `REDIS_URL`, `LOG_LEVEL`). The field mapping itself was never broken; only the docs and error-message text named a variable the process silently ignored. Confirmed the ignored-var behavior with `FORWARD_MAX_ATTEMPTS=9` under the wrong prefix (stayed at default 3) vs. the correct name (took effect). Also caught two tests in `test_logging.py` that set `POKEPROXY_FORWARD_MAX_ATTEMPTS=1` expecting a fast single-attempt run — silently ignored, so those tests were passing by coincidence (3 attempts still fit inside the test's own timing budget), not for the reason claimed. Fixed both.

All four operational settings (`forward_max_attempts`, `forward_deadline_seconds`, `redis_connect_timeout_seconds`, `redis_socket_timeout_seconds`) now share one `_check_positive_seconds` validator keyed off `pydantic.ValidationInfo.field_name`, so the error message always names the variable the process actually reads. 8 new tests in `test_config.py` (48 → 56) proving each of the four env vars actually takes effect and that non-positive values are rejected by name — this is the test category that was missing and let the original naming bug ship unnoticed. `.env.example`/`README.md`/both issue docs corrected. Not committed — done as a direct follow-up request, no new `docs/issues/` entry opened for it since it's a correction to already-documented C2/C4, not a new Part 1 issue.

**H1 — rules loaded once at startup (Wave 3).** `main.py` gains `_load_rules(config_path)`, mirroring `_load_settings`: calls `load_rules`, and on `OSError`/`ValueError` (missing file, malformed JSON, or any of `load_rules`'s own validation errors) logs one `CRITICAL` line and `raise SystemExit(1)` instead of letting the app start with a config it can't use. `lifespan()` calls it once and stores the result on `app.state.rules`, replacing `app.state.config_path`. `proxy.py` no longer imports or calls `load_rules` — `stream()` reads `request.app.state.rules` directly.

Verified: 6 new tests (`test_startup.py`, 56 → 62) — `app.state.rules` populated at startup; `load_rules` patched and counted across 3 requests through `TestClient`, confirming exactly one call; invalid rule / malformed JSON / missing file each raise `SystemExit`; the failure logs at `CRITICAL` naming the reason. `ruff check .` clean. Full detail in `docs/issues/006-rules-reloaded-per-request.md`.

**M1 + H7 — liveness/readiness split (Wave 4).** `/health` is unchanged — cheap, always trivially true. New `GET /ready` reads `app.state.ready`, a bool set `True` at the end of `lifespan`'s startup section and `False` as the first line after `yield` resumes, before the Redis/HTTP client cleanup runs. Returns 503 when false. Deliberately does not probe Redis — a Redis blip must not un-ready every pod at once, matching the readiness decision already recorded in `docs/planning/part-01-production-hardening.md`. `/ready` joins `/health`/`/stats` in `_UNLOGGED_PATHS`. Naming considered and rejected the `/healthz`+`/readyz` convention in favor of keeping the existing documented `/health` and adding `/ready` — smaller diff, no rename risk.

This is the app-side half only. The K8s-side `preStop`/`terminationGracePeriodSeconds` wiring that gives the flip time to actually stop traffic before `SIGTERM` is Part 2 (Backlog).

Verified: 5 new tests (`test_readiness.py` ×4, one in `test_logging.py`; 62 → 67) — `/ready` is 200 after startup, 503 when forced not-ready, `app.state.ready` is false after the app's lifespan shutdown runs, `/health` is unaffected by readiness state, `/ready` produces no access line. `ruff check .` clean. Full detail in `docs/issues/007-liveness-readiness-split.md`.

**H2 + H3 — header hygiene in both directions (Wave 4).** `STRIP_HEADERS` (a denylist) is replaced by `ALLOWED_FORWARD_HEADERS` (an allowlist), currently empty — no original client header reaches downstream; the proxy already builds every header downstream needs itself (`Content-Type`, `X-Grd-Reason`, `X-Request-ID`), and nothing downstream reads anything else (confirmed: `mock_service` only reads `X-Grd-Reason`). New `_forwardable_response_headers` strips the RFC 7230 hop-by-hop set plus `Content-Length`/`Content-Encoding` from the downstream response before it reaches the client — kept as a corrected blocklist rather than an allowlist on this side, since the response comes from a trusted, configured downstream URL and an allowlist there risks silently dropping legitimate business headers.

Verified: 6 new tests (`test_headers.py`, 67 → 73) — unit tests on both filter functions directly, plus two end-to-end tests through `TestClient` with a mocked downstream confirming `Authorization`/`Cookie` never reach downstream while `X-Grd-Reason` does, and a mocked downstream's `Connection` header never reaches the client while a custom header does. `ruff check .` clean. Full detail in `docs/issues/008-header-hygiene.md`.

**H4 + H5 — outcome accounting fixed, unbounded response-time storage removed (Wave 4).** New `EndpointStats.record_request(is_error: bool)` replaces two independently-incremented counters with one atomic call, used at all three exit points of `_forward_request` — `error_count <= request_count` can no longer drift apart, closing the exact bug where `error_rate` read `0.0` during a total downstream outage. `bytes_received` changed from `=` to `+=`. New `StatsRegistry.record_outcome(outcome: str)` gives rejections and `no_rule_matched` — which have no downstream URL to key on — their own flat `{outcome: count}` map, via a new `_outcome_response()` helper in `proxy.py` that collapses every rejection branch into one call (mirrors C5's `request.state.outcome` seam, applied to accounting). `main.py`'s `internal_error` handler gets the same one-line treatment. `StatsRegistry.to_dict()` shape changes to `{"endpoints": {...}, "outcomes": {...}}` — no test or documented consumer relied on the old flat shape.

**Follow-up, same session — `_response_times`/`percentile()` deleted, not bounded.** First pass bounded the list to `deque(maxlen=1000)`. User pushed back: percentiles belong to Prometheus/Grafana in Part 4 (`histogram_quantile()` over real histogram buckets), and a hand-rolled bounded sample was complexity the app doesn't need to own for a capability with a known short shelf life. Deleted the structure and the method entirely instead of bounding it. `avg_response_time` untouched — it was already O(1) memory (`total_response_time`/`request_count`) and was never the source of the H5 bug.

Verified: 14 new tests (`test_stats.py`, 73 → 87) — unit coverage on both data structures, plus end-to-end proof through `TestClient` that 3 simulated downstream failures in a row now produce `error_rate == 1.0`, not `0.0`; a rejected request and a `no_rule_matched` request are both counted by outcome; an unhandled exception is counted as `internal_error`; `bytes_received` accumulates across two requests instead of being overwritten. `ruff check .` clean. Full detail in `docs/issues/009-outcome-accounting-and-unbounded-stats.md`.

**M4 — cache becomes a dedup layer (Wave 3).** `cache.py` now stores the downstream **response** (status, filtered headers, base64-encoded content in a JSON envelope), not the decoded payload — `get_cached_response`/`cache_response` replace `get_cached_pokemon`/`cache_pokemon`. A hit in `stream()` short-circuits immediately via `_duplicate_response()`: no decode, no rule match, no forward, `request.state.outcome = "duplicate_suppressed"`, recorded through H4's outcome-keyed stats seam, replays the stored response byte-for-byte. A miss proceeds as before; `_forward_request` caches the response itself, inline in its success branch, only when a real downstream response came back (`forwarded`) — `downstream_timeout`/`downstream_error` are never cached, so a duplicate arriving after downstream recovers gets a fresh attempt rather than a replayed failure. `Settings.cache_ttl_seconds` (`CACHE_TTL_SECONDS`, default 300.0) joins the four existing operational settings sharing `_check_positive_seconds`.

User's design call, and a genuine improvement over what was originally proposed (a synthetic "duplicate" marker): replaying the real response is fully transparent to the client, matching how idempotency-key caching works in production systems. Duplicate replays deliberately do **not** touch per-URL `EndpointStats` — no network call happened, so counting one there would misrepresent real downstream traffic volume.

Verified: `test_cache.py` rewritten for the new API (11 tests) — hit/miss, arbitrary binary content round-trips through the base64 envelope, C3's no-keyspace-scan regression preserved, C4's failure-degrades-to-miss/no-op regression preserved. `test_config.py` +2 parametrized cases for `CACHE_TTL_SECONDS`. New `test_dedup.py` (5 tests), end-to-end through `TestClient` with a fake Redis and a mocked downstream: a duplicate replays the cached response and downstream is called exactly once, not twice; the replay is counted as `duplicate_suppressed`; it does not inflate `EndpointStats.request_count`; a downstream failure is never cached — a retried duplicate after recovery gets a fresh, successful attempt; two different payloads are never deduplicated against each other. Full suite: 86 → 94. `ruff check .` clean. Full detail in `docs/issues/010-cache-becomes-a-dedup-layer.md`.

**M7-CWD — test suite pinned to `app/` regardless of invoking shell (Wave 5).** New `app/tests/conftest.py`, four lines: a `pytest_configure()` hook that `chdir`s to the directory containing the test files' own parent, computed from `Path(__file__)` rather than assumed from the invoking shell. No application code changed — `POKEPROXY_CONFIG` staying a relative path is correct for every real deployment (container `WORKDIR`, or `cd app && uvicorn ...` locally); this was purely a test-harness gap, not an app bug.

Verified by direct multi-directory invocation rather than a new pytest test (a test that verifies "pytest works" would be circular): 94/94 passed identically from `app/`, the repo root, and `/tmp` — up from 59 passed / 35 failed from the repo root before the fix. `ruff check .` clean. Full detail in `docs/issues/011-test-suite-cwd-dependence.md`.

**C5 — structured logging (Wave 2).** stdlib `logging` plus a JSON formatter in a new `logging_config.py`; no new dependency, and one setup unifies uvicorn's own output instead of leaving it plaintext alongside ours. Uvicorn's access log is replaced by our own middleware line because it cannot carry a correlation ID, latency or outcome. Correlation via `X-Request-ID` — deliberately the infrastructure convention rather than an `X-Grd-*` name, so the trace survives the ingress. Handlers label `request.state.outcome`; the middleware is the single place that reads it and emits one access line. Unhandled exceptions become a JSON 500 carrying the request ID rather than a raw ASGI traceback. Also folded in the C1 leftover: bad config is now one CRITICAL line.

Kept deliberately simple — no `contextvars`, no outcome enum, no console log format.

Verified: 12 new tests (16 → 28); the same 5-case probe used for the "before" measurement went from **116 plaintext lines to 15 structured JSON records** (plus 88 traceback lines, kept multi-line on purpose); the two 401s are now distinguishable; `ruff check .` clean. Full detail in `docs/issues/002-structured-logging.md`.

---

## Part 2 — Infrastructure & Deployment

Design agreed 2026-08-23. Full reasoning, alternatives and the constraints this sets for later Parts: `docs/planning/part-02-infrastructure-deployment.md`. Only decisions and measured results go here.

**Stack:** WSL Ubuntu + bash · k3d · Helm (one chart, `values-local` + `values-prod`) · `python:3.13-slim-bookworm` multi-stage built with pinned uv 0.12.5 · Redis templated in-chart on the official `redis:7-alpine` · Sealed Secrets with a pinned sealing key · Traefik ingress exposing `/stream` only.

**Decisions that overruled my initial recommendation, both deliberate:**
- **Helm instead of Kustomize.** My case for Kustomize was `configMapGenerator`'s content-hash naming (which solves the H1 rules-restart problem for free) plus `kustomize edit set image`. Overruled on consistency — one packaging tool for local and prod. Helm's `checksum/config` annotation is equivalent, and `helm upgrade --atomic` plus `helm rollback` revision history is a real gain back for Part 3.
- **CPU limits at 2× requests.** I argued for requests-only (CFS throttling on a latency-sensitive proxy costs tail latency, which then poisons Part 4's alert thresholds). Overruled. Mitigation: the *requests* come from `kubectl top` measurement in step 9, not from a guess.

**Decision where I pushed back and it stuck:** Redis is templated in our own chart rather than pulled from the Bitnami chart. The Aug 2025 catalog change moved Bitnami's versioned images to `docker.io/bitnamilegacy/*` (archived, unpatched) and stopped OCI chart publishing; and `architecture: standalone` still brings a StatefulSet, PVC, auth Secret and sentinel/metrics templates we would disable. ~50 lines of our own YAML on the official image, with `maxmemory` set strictly below the container memory limit so Redis evicts under LRU instead of being OOMKilled.

**Sealing key must be pinned or nothing is reproducible.** The controller mints a fresh keypair when it finds no Secret labeled `sealedsecrets.bitnami.com/sealed-secrets-key: active`, so on an ephemeral k3d cluster a committed SealedSecret would stop decrypting after every recreate. Bootstrap generates the keypair into a gitignored `.secrets/`, applies it *before* installing the controller, and installs with `keyrenewperiod=0`.

**Step 1 (Docker image) — done 2026-08-23.** `app/Dockerfile` + `app/.dockerignore`. Multi-stage: uv copied from `ghcr.io/astral-sh/uv:0.12.5`, dependency layer keyed only on `pyproject.toml` + `uv.lock`, then `uv sync --frozen --no-dev --no-editable` so the runtime stage is the venv alone — no uv, no compilers, no source tree. `POKEPROXY_CONFIG` defaults to the absolute `/etc/pokeproxy/rules.json`, which kills the CWD-relative bug class (M7) at the deployment layer too.

Verified by execution, not asserted:

| Check | Result |
|---|---|
| Build (cold) | 37.0s |
| Image size | **248 MB** |
| Runtime user | `uid=10001(pokeproxy) gid=10001(pokeproxy)` — numeric, because `runAsNonRoot: true` cannot validate a named user |
| Container start → `startup complete` | **2.55s**, including Docker's own start overhead |
| `--read-only --cap-drop ALL --security-opt no-new-privileges` | Serves normally; `docker diff` shows only the rules bind-mount — **zero filesystem writes** |
| Logs | JSON from the first line, uvicorn's records included |
| Dev dependencies | `pytest` absent; venv `bin/` holds only runtime entry points |
| SIGTERM | `shutdown started` → `shutdown complete` → exit **0** in 880 ms. uvicorn is PID 1 (exec-form `CMD`) — no signal-forwarding or zombie-reaping problem |
| 5-case signed probe + 3 ops endpoints | 200 `{}` no-rule-matched · 502 downstream error (**expected** — rules still say `localhost:8001`, H6) · 401 missing sig · 401 bad sig · 400 bad protobuf · `/health` `/ready` `/stats` all 200 with populated counters |
| Redis unreachable in-container | 3 × `WARNING cache lookup failed`, **zero 5xx** — C4 degradation holds in the container |

Startup at 2.55s contradicts the ~3.2s module-import figure from Part 1, as expected — that number was WSL `/mnt/c` filesystem overhead, not the application. The `startupProbe` budget will be set against the container figure.

No Python changed in step 1, so the test suite was not re-run.

Base images are pinned by tag, not digest. Digest pinning is stronger and belongs in Part 3 once there is a bot to bump them.

**Step 2 (config preflight entrypoint) — done 2026-08-23.** New `src/pokeproxy/__main__.py`: reuses `main.py`'s existing `_load_settings()`, then hands off to `uvicorn.run(..., log_config=None)`. `Dockerfile` `CMD` → `["python", "-m", "pokeproxy"]`. Closes **R4** (bad config previously produced the intended `CRITICAL` line *and then* uvicorn's own ~20-line lifespan `SystemExit` traceback — validating before `uvicorn.run()` starts removes the second part) and **M6** (`POKEPROXY_PORT` was validated and documented but nothing read it; the entrypoint is the one place that now does).

`log_config=None` is load-bearing: `pokeproxy.main`'s import-time `setup_logging()` clears the `uvicorn`/`uvicorn.error`/`uvicorn.access` handlers and sets `propagate=True` so their records reach the JSON handler on root. `uvicorn.run()` left at its default would call its own `dictConfig()` afterward and silently reinstall handlers with `propagate=False`, undoing that. Caught by a dedicated test and confirmed live — uvicorn's own startup lines still render as JSON in the container.

Also added `.gitattributes` at the repo root (`eol=lf` for `*.sh`, `Dockerfile*`, `*.yaml/.yml`, `Makefile`) — folded in here rather than deferred, since `git add` warned "LF will be replaced by CRLF" on every file in step 1's commit, and a CRLF `.sh` fails with `bad interpreter: /bin/bash^M` under WSL or in a container, which is exactly the failure mode Part 5's bootstrap scripts would hit.

Five new tests in `tests/test_entrypoint.py` (`uvicorn.run` mocked, no real port bound): bad config exits before `uvicorn.run` is called; a custom `POKEPROXY_PORT` reaches the `port` kwarg; default is 8000; app import string is `"pokeproxy.main:app"`; `log_config=None` is passed.

Verified by execution:

| Check | Result |
|---|---|
| `ruff check .` | All checks passed |
| `pytest -q` from `app/`, repo root, `/tmp` | **106 passed** each time (101 → 106) — M7-CWD independence survives the new entrypoint |
| Container, `POKEPROXY_HMAC_KEY` unset | **1 line** of output, `CRITICAL configuration invalid, refusing to start`, exit **1** |
| Container, `POKEPROXY_PORT=9001` | `/health` answers on 9001; log line reads `Uvicorn running on http://0.0.0.0:9001` |
| Container, SIGTERM on that run | clean drain, exit 0, 1.21s wall |
| JSON logging through the new path | uvicorn's own lines still JSON — no regression from `log_config=None` |

No change to `main.py`, `proxy.py`, `config.py`, or any request-path code — entrypoint-only.

**Step 3 (mock downstream image) — done 2026-08-23.** New `app/Dockerfile.mock`, closing **L6**. Deliberately doesn't reuse `app/uv.lock` — the shared lockfile drags in `httpx`, `protobuf`, `redis`, `pydantic-settings`, none of which the mock uses — instead pins `fastapi==0.135.1` / `uvicorn[standard]==0.41.0` directly to the versions already resolved there, so the two images can't drift on framework version without it being a deliberate bump. `PYTHONPATH=/app` makes `mock_service.main:app` importable without packaging it into a wheel, which is the actual L6 decision (separate image, source copied in, not installed).

`mock_service/main.py` gained `GET /health` (plain liveness — no dependencies to gate on). **Applied the new code-style rule immediately:** deleted the `if __name__ == "__main__": uvicorn.run(app, host="127.0.0.1", ...)` block — unexercised (the documented run path is always the `uvicorn` CLI) and its `127.0.0.1` bind was literally the H6 backlog's `mock_service/main.py:34` citation. Superseded by the current change, removed as part of it rather than left behind.

Verified by execution:

| Check | Result |
|---|---|
| Build | 16.8s cold |
| Image size | **236 MB** |
| Runtime user | `uid=10001(mockdownstream)` |
| `--read-only --cap-drop ALL --security-opt no-new-privileges` | Serves normally; `docker diff` shows **zero writes** |
| Bind | `0.0.0.0:8001`, confirmed via the published port |
| `GET /health` | `{"status":"alive"}`, 200 |
| `POST /pokemon` → `GET /received` | Body and `X-Grd-Reason` both land correctly |
| `ruff check .` / `pytest -q` | Clean / **106 passed** — no regression; `mock_service` was and remains outside pytest coverage (a test double testing a test double is circular) |

**Step 4 (Helm chart skeleton) — done 2026-08-23.** New `deploy/helm/pokeproxy/`: `Chart.yaml`, `.helmignore`, `values.yaml`, `templates/{_helpers.tpl, namespace.yaml, serviceaccount.yaml}`. Workload manifests (redis, mock-downstream, pokeproxy Deployments/Services) are step 5, not this one.

`values.yaml`'s `components` map (`pokeproxy`, `mock-downstream`, `redis`) uses kebab-case keys matching the actual Kubernetes resource names — no camelCase-to-kebab mapping table to keep in sync. Each carries `enabled` + `serviceAccount.create`, which is what lets `values-prod.yaml` (step 10) turn the mock off with one line.

**Caught and fixed a naming stutter before it shipped:** the naive `<release>-<component>` pattern renders `pokeproxy-pokeproxy` when the release is named `pokeproxy` (as step 6 plans) and the component is the main app, since the component name collides with the chart name. `pokeproxy.component.fullname` in `_helpers.tpl` special-cases that one collision — the same fix `helm create`'s own scaffold applies for its single main component, adapted here for three. Verified: `pokeproxy`, `pokeproxy-mock-downstream`, `pokeproxy-redis`, and confirmed to still hold under a different release name (`myrelease` → `myrelease`, `myrelease-mock-downstream`, `myrelease-redis`).

`namespace.yaml` names itself `{{ .Release.Namespace }}` rather than a separate `values.namespace` field — one fewer value that could silently disagree with the other. Carries `pod-security.kubernetes.io/{enforce,audit,warn}: restricted`, per the design decision to make step 5's securityContext an enforced invariant rather than a claim.

**Corrected in step 6 — this claim was wrong.** Tested live: `helm install -n pokeproxy` without `--create-namespace` fails outright (`namespaces "pokeproxy" not found`) regardless of the chart owning a Namespace resource, and `--create-namespace` collides with that same resource on ownership metadata. `templates/namespace.yaml` was removed from the chart; the namespace + PSA labels are now applied via `kubectl` before `helm upgrade --install` runs. Full detail in the step 6 entry below.

Verified by execution:

| Check | Result |
|---|---|
| `helm lint . --strict` | Clean — only an informational "icon is recommended" note |
| `helm template pokeproxy . --namespace pokeproxy` | Renders 1 Namespace + 3 ServiceAccounts; PSA labels present; consistent `app.kubernetes.io/{name,component,part-of,instance}` on every resource |
| `components.mock-downstream.enabled=false` override | Mock's ServiceAccount correctly absent, others unaffected |
| Different release name (`myrelease`) | Fullname fix holds — not hardcoded to one release name |

No Python touched — chart-only step, test suite not re-run.

**Step 5 (workload templates) — done 2026-08-23.** New Deployment + Service per workload under `templates/{pokeproxy,mock-downstream,redis}/`, plus `pokeproxy-env`/`pokeproxy-rules` ConfigMaps. Closes the rest of **H6** and **H7**'s cluster-side half.

**H6 closed for real, not just relocated.** `values.yaml` rules hold only `reason`/`match`; `configmap-rules.yaml` computes the downstream URL from the mock Service's own naming helper + `.Release.Namespace`, so it can't drift from the Service that actually exists. Verified past "renders": piped the rendered `rules.json` through the real `pokeproxy.rules.load_rules()` — parses into the identical 3 `Rule` objects the local `config/rules.json` produces, URL swapped to `http://pokeproxy-mock-downstream.pokeproxy.svc.cluster.local.:8001/pokemon`.

**Caught a Go-json quirk, not a bug:** Sprig's `toJson` HTML-escapes `<`/`>` as `<`/`>` (a Go `encoding/json` default for HTML embedding, irrelevant here). `json.loads` decodes it identically, so never functional — but `kubectl describe configmap` would have shown garbled escapes on 3 of 4 match conditions. Fixed with `| replace "\\u003c" "<" | replace "\\u003e" ">"`.

**H7 cluster-side closed:** `lifecycle.preStop.sleep.seconds: 5` on every workload — the app-side drain (112ms, Part 1) was already correct; this covers the "endpoint deregistration is async with SIGTERM" gap that was explicitly scoped to Part 2.

**checksum/config-{env,rules} annotations close the H1 consequence** (a rules edit was previously inert until a manual restart). Verified with 3 renders: a rule-content change moves `checksum/config-rules` and leaves `checksum/config-env` untouched; an unrelated redis-only value moves neither.

**Redis uid/gid verified against the real image, not guessed.** `id redis` inside `redis:7-alpine` reports `uid=999(redis) gid=1000(redis)` — group is 1000, not the 999:999 I'd have assumed. Guessing wrong here means a permission-denied crash loop the first time the `emptyDir` needs a write. `fsGroup: 1000` at the pod level (not `runAsGroup` alone) is what makes the volume writable by that GID at mount time. Verified live: `redis:7-alpine --user 999:1000 --read-only` against a volume pre-chowned to `999:1000`, with the chart's exact `--maxmemory 128mb --maxmemory-policy allkeys-lru` args — `PONG`, a real `SET`/`GET`, `maxmemory` reporting exactly 134217728 bytes, `maxmemory-policy` correctly `allkeys-lru`, clean startup log.

**Image tags default to `CHANGEME`, not a plausible-looking fallback.** The design already committed to immutable git-sha tags for the two images this project builds; a fallback like `.Chart.AppVersion` would silently deploy the wrong (or no) image if an operator forgets `--set image.tag=$(git rev-parse --short HEAD)`. `CHANGEME` fails loud (`ErrImagePull` naming an unmistakable tag) instead of quietly wrong. Redis keeps a real default (`7-alpine`) — it's a pinned upstream version, nothing to forget.

**Contract for step 7:** `envFrom.secretRef.name: pokeproxy-hmac` on the pokeproxy container, namespace `pokeproxy`, one data key literally `POKEPROXY_HMAC_KEY`. Not `optional: true` — a missing Secret should leave pods in `CreateContainerConfigError`, the intended fail-fast.

Verified by execution:

| Check | Result |
|---|---|
| `helm lint . --strict` | Clean |
| `helm template` (fake tags via `--set`) | 12 resources, zero errors |
| Rendered `rules.json` → real `load_rules()` | Parses correctly, URL correctly cluster-internal |
| `checksum/config-{env,rules}` isolation | Confirmed via 3 comparative renders |
| Service selectors vs. Deployment pod labels | Cross-checked programmatically — every Service matches exactly one Deployment |
| `serviceAccountName` on every Deployment | Resolves to an SA the chart actually renders |
| Redis uid 999/gid 1000 + `fsGroup` | Live container round-trip, see above |

**Not yet verified — needs a real cluster (step 6):** probes passing against live pods, `checksum/config` actually triggering a rollout on a live `helm upgrade`, and pokeproxy→redis / pokeproxy→mock-downstream cluster-DNS resolution.

No Python changed — chart-only step; `load_rules()` was used as a verification tool, not modified.

**Step 5 follow-up (user review, same day).** Probe timing (`periodSeconds`/`timeoutSeconds`/`failureThreshold`, plus `path` for HTTP probes) moved into `values.yaml` for all three workloads — redis's `exec` command itself stays hardcoded since that's the check's identity, not a tunable. Verified the default render is byte-identical to the prior hardcoded values, then confirmed two independent `--set` overrides each land only on their own resource. Confirmed (not changed) the rules-file path chain: Dockerfile's `POKEPROXY_CONFIG=/etc/pokeproxy/rules.json` → ConfigMap key `rules.json` → whole-directory volume mount at `/etc/pokeproxy`, no `subPath` → exact match, already proven in step 5 via `load_rules()`. Full detail in the planning doc.

**Step 6 (first real cluster) — done 2026-08-23.** Installed `kubectl` v1.30.5 and `k3d` v5.9.0 in WSL, pinned binaries from each project's own official release channel. New `deploy/k3d/cluster.yaml`: 1 server, 0 agents, `image: rancher/k3s:v1.35.5-k3s1` pinned explicitly rather than left floating. Built and imported both images at the current sha, `helm upgrade --install` into the cluster.

**Two real bugs found by actually running it, both fixed — not hypothetical, not deferred:**

1. **The step-4 Namespace design was wrong.** Tested live: `helm install -n pokeproxy` without `--create-namespace` fails outright (`namespaces "pokeproxy" not found`) even though the chart owns a Namespace resource — Helm requires the target namespace to exist before applying anything, contradicting what step 4 assumed about apply ordering. `--create-namespace` doesn't fix it either: it creates the namespace via an untracked raw call, and the chart's own Namespace resource then collides on ownership metadata (`namespaces "pokeproxy" already exists`). Both errors captured verbatim before changing anything. **Fix:** removed `templates/namespace.yaml` from the chart; namespace + PSA/ownership labels now applied via `kubectl create namespace --dry-run=client -o yaml | kubectl label --local -f - ... | kubectl apply -f -` before Helm runs — exactly the sequence Part 5's bootstrap will script.
2. **`mock-downstream` had no `startupProbe`, and it mattered.** `kubectl describe` showed `Killing ... Container mock-downstream failed liveness probe, will be restarted` on the very first deploy — the default `initialDelaySeconds: 0` let the liveness probe start counting failures before the container had bound its port on a cold, freshly-imported image. Step 5's reasoning ("fast native startup, no concern") didn't hold under a real cold start. **Fix:** added the same `startupProbe` pattern pokeproxy already had to `mock-downstream` and `redis`. Re-verified with `helm upgrade --install --wait --timeout 3m`: succeeded first try, **0 restarts** across all 4 pods.

**Temporary HMAC secret, deliberately not committed:** `kubectl create secret generic pokeproxy-hmac -n pokeproxy --from-literal=POKEPROXY_HMAC_KEY=<the documented dev key>` — manual, session-local, same name/key the chart already expects. Step 7 replaces the provisioning mechanism, not the contract.

Verified by execution:

| Check | Result |
|---|---|
| `k3d cluster create` | 87s; node Ready; all `kube-system` pods healthy |
| `k3d image import` | Both images imported, 68s |
| `helm upgrade --install --wait --timeout 3m` | Succeeds; **all 4 pods 1/1 Running, 0 restarts** |
| DNS inside a pokeproxy pod | Both Service FQDNs resolve to their exact ClusterIPs |
| Signed request via `kubectl port-forward` → `/stream` | `200 {"status":"received"}` |
| Same request read back from mock-downstream | `GET /received` shows the exact payload + correct `reason` — proves rule matching, not just a 200 |
| Repeat of the identical payload | `200` again (cache replay) + `/stats` shows `duplicate_suppressed: 1` — proves Redis GET/SET genuinely round-trips over cluster DNS |
| `kubectl top pods` | Works — metrics-server functional, idle-state numbers only, not step 9's load measurement |
| `helm history` | Revisions 1 (superseded) + 2 (deployed) — a rollback target already exists |

**Not yet verified, explicitly out of scope here:** Traefik/ingress (step 8; running idle as a k3d default, wired to nothing), `checksum/config` triggering a live rollout (step 9), real load-based resource measurement (step 9). Full detail, including the exact failing commands for both bugs, in `docs/planning/part-02-infrastructure-deployment.md`.

No Python changed — `app/` was used only as a verification client (building real signed protobuf requests), not modified.

**Step 7 (Sealed Secrets) — done 2026-08-23.** Replaced step 6's manual `kubectl create secret` with the real flow: controller (`sealed-secrets/sealed-secrets` v2.19.3, `ghcr.io/bitnami/sealed-secrets-controller:0.39.1`, `fullnameOverride=sealed-secrets-controller`, `keyrenewperiod=0`), `kubeseal` v0.39.1 pinned to match, a self-signed sealing keypair persisted at gitignored `.secrets/sealing-key.yaml`, new `templates/pokeproxy/sealedsecret-hmac.yaml`, new `values-local.yaml` holding the real ciphertext, and the actual deliverable script `scripts/seal-hmac.sh` (idempotent: generates the key only if absent, reseals only if `values-local.yaml` is absent or still `CHANGEME`).

**The design's central claim was actually tested, not asserted:** `k3d cluster delete pokeproxy` → recreate → `bash scripts/seal-hmac.sh` (reused the existing local sealing key; controller log confirmed `registered private key`, never `generated new key`) → namespace/images/`helm upgrade --install -f values-local.yaml --wait` → **succeeded first try, 0 manual steps, 0 restarts** → a fresh signed request through a new `port-forward` returned `200 {"status":"received"}`. `values-local.yaml`'s ciphertext is byte-identical before and after the cycle.

Sealed the *existing* documented dev secret (`.env.example`'s value), not a fresh random one — consistent with L1's already-accepted reasoning that a shared local-dev secret is a documented convenience. Keeps `load_generator.py` and every manual verification script working unmodified against this cluster; overridable via `POKEPROXY_HMAC_KEY` env var for anyone reusing the script with a real secret.

**One process hiccup, not a design bug:** first deploy attempt this step hit `ImagePullBackOff` — `pokeproxy:146c88a` was never built/imported (only `da102ba` had been, from before this session's commit). Rebuilt both images at the current sha, reimported, retried successfully. A live reminder of why CI always builds at the exact sha it deploys.

Verified by execution:

| Check | Result |
|---|---|
| `helm lint . --strict` (with `values-local.yaml`) | Clean |
| Controller adopts the pinned key | Confirmed via log, both before and after cluster recreation — never mints a new one |
| `values-local.yaml` ciphertext | Byte-identical across the delete/recreate cycle |
| Fresh-cluster deploy, `--wait --timeout 3m` | Succeeds first try, 0 manual steps, 0 restarts |
| Signed request on the recreated cluster | `200 {"status":"received"}` (matching payload); `200 {}` (non-matching, still proves signature verification passed) |
| `pokeproxy-hmac` Secret ownership | `ownerReferences` points at the `SealedSecret` CR, `controller: true` |

**Not yet verified, explicitly out of scope:** a genuinely fresh clone with no local `.secrets/` at all (documented, accepted trade-off — generates a new key and reseals); `values-prod.yaml`'s equivalent (step 10, no production cluster to seal against).

No Python changed.

**Step 8 (Ingress + NetworkPolicy) — done 2026-08-23.** Traefik, idle since step 6, finally wired up. New `templates/pokeproxy/{ingress,traefik-middleware}.yaml` and `templates/networkpolicy.yaml`, values-gated. `deploy/k3d/cluster.yaml` gained `8080:80@loadbalancer` (k3d port mappings are cluster-creation-time only, so this meant a full recreate — cheap and safe now, per step 7's proof).

**Ingress exposes only `/stream`**, no rules for `/health`/`/ready`/`/stats` — they 404 at the edge, closing the M5 partial mitigation without needing an explicit deny.

**M2 proven to be the actual rejecting layer, not coincidentally agreeing with the app's own check.** A >1 MiB request returns 413 with Traefik's plain-text body (`Request Entity Too Large`, not the app's `{"error": ...}` JSON), and the app's own access log shows **zero trace of the request** — it never left the edge. A bare 413 alone would have been ambiguous between the new Middleware and the app's pre-existing `MAX_BODY_SIZE` check; this distinguishes them.

**Real templating bug, caught before the cluster saw it:** `maxRequestBodyBytes` rendered as `1.048576e+06` — Helm decodes YAML numbers as `float64`, and Go's default float formatting picks scientific notation for round values. Fixed with `| int`. General Helm/Sprig gotcha, not specific to this field — worth remembering for any future numeric value read from `values.yaml`.

**Two genuinely uncertain things about k3s, both tested rather than assumed:**
- Does k3s's default NetworkPolicy controller enforce anything? Proven with a real A/B: an unlabeled, PSA-compliant `busybox` pod resolved `pokeproxy-redis`'s DNS in 0.09s but failed TCP to both redis and mock-downstream (~1s, exit 1) every time; the actual pokeproxy pod connected to redis in 89ms on the same policy set. Same target, different pod identity, different outcome.
- Does kubelet probe traffic get blocked by a default-deny ingress policy? No explicit allow exists for it anywhere in the templates, yet `helm upgrade --install --wait` succeeded with all 4 pods reaching Ready — k3s doesn't subject node-originated probe traffic to pod-to-pod policy enforcement.

**Incidental proof of a step-4 claim that was still unverified:** the first attempt to create the plain debug pod was rejected outright by the API server — `violates PodSecurity "restricted:latest": allowPrivilegeEscalation != false, ...runAsNonRoot != true...`. Exactly the "apply a non-compliant pod, confirm PSA rejects it" check the original plan called for, arriving as a side effect.

**A third occurrence of the step-6 probe-timing bug class, fixed more broadly this time.** `mock-downstream` failed its `startupProbe` and restarted once on this fully-fresh cluster — likely more scheduling contention than step 6 had (sealed-secrets controller + all 4 app pods at once, on a cold node). Since pokeproxy didn't flake this run but mock-downstream did, and nothing guarantees the same workload flakes next time, widened `startupProbe.failureThreshold` 30→60 for **all three workloads symmetrically**, not just the one that happened to fail. Redeployed: zero restarts across all 4 pods.

Verified by execution, all five step-8 bullets:

| Check | Result |
|---|---|
| Signed request from the host through the ingress | `200 {"status":"received"}` via `http://localhost:8080/stream`, no port-forward |
| >1 MiB rejected at the edge | `413`, Traefik's own error text, zero trace in the app's access log |
| `/stats`/`/health`/`/ready` not reachable via ingress | `404` for all three |
| proxy→redis allowed, everything else denied | Unlabeled pod: DNS resolves, TCP fails to both redis and mock-downstream. Pokeproxy pod: TCP to redis succeeds in 89ms |
| DNS still resolves | Confirmed via the unlabeled pod's `nslookup` and every successful forward this step |

No Python changed.

**Step 9 (rollout/termination/measurement pass) — done 2026-08-23.** Three things proven live that the chart's YAML alone couldn't prove.

**Rolling restart under real load, via the real ingress (not port-forward).** `scripts/load_generator.py` at 30 rps / 100s against `http://localhost:8080/stream`, `kubectl rollout restart deployment/pokeproxy` triggered mid-run. **2487 sent, 0 errors, 0.0% error rate**, all 4 pods finished at 0 restarts. `preStop.sleep.seconds: 5` + `maxUnavailable: 0` did exactly their job.

**Resource measurement — confirmed the step-5 numbers, changed nothing.** Sampled `kubectl top` through the rollout run plus a second 100 rps / 25s burst immediately after `redis-cli FLUSHALL` (forces real forwards, not cache replay). Peak pokeproxy CPU: **224m, during the rollout itself** (cold-start + full traffic share at once) — under the 250m request, nowhere near the 500m limit. Steady-state (both dedup-heavy and freshly-flushed): 2–26m across all three workloads; memory never moved from idle. **`values.yaml` unchanged** — this validated the provisional numbers rather than replacing them, which is a legitimate outcome for a measurement pass and worth stating plainly rather than tuning for the sake of showing motion. Caveat on record: this is dedup-heavy traffic by construction (12 fixed payloads); Part 3's load-generator uniqueness fix is the first chance to measure genuinely sustained fresh-forward load.

**Rules edit → live rollout, proven functionally this time, not just structurally (step 5 only proved the checksum changes on paper).** Built a payload matching no current rule (`200 {}`), lowered the fire-rule's attack threshold via a values **file**, redeployed — new ReplicaSet, `--wait` succeeded, same payload now returns `200 {"status":"received"}`. Reverted; confirmed clean.

**A real Helm bug, hit and diagnosed:** first attempt used `--set` to mutate one index of a list-nested-in-a-list — the new pod went **CrashLoopBackOff**, `"Invalid condition syntax: 'None'"`. `--set` on nested list indices doesn't merge cleanly with the base values; a documented Helm fragility. `maxUnavailable: 0` meant the two old pods kept serving the whole time — the cluster itself was never degraded, only that one rollout attempt was stuck. Fixed with a proper values file instead, verified via `helm template` before touching the cluster again. The failed revision is preserved in `helm history` — `helm rollback` was available the whole time; fixed forward instead.

**A second lesson, about the app's own semantics:** the first post-revert check looked like the revert had failed (`200 {"status":"received"}` when `200 {}` was expected) — actually a stale Redis dedup hit, since dedup checks run *before* rule evaluation. `redis-cli FLUSHALL` plus a retest gave the real answer (`200 {}`, revert confirmed correct). Worth remembering: a repeated payload proves nothing about current rules unless the cache is accounted for.

Verified by execution:

| Check | Result |
|---|---|
| Rolling restart, 30 rps live load, real ingress | 2487 sent, **0 errors**, 0 pod restarts |
| Resource peak (pokeproxy) | 224m CPU at rollout vs. 250m request / 500m limit |
| Resource steady-state, all 3 workloads | 2–26m CPU; memory unchanged from idle |
| Rules edit reaching running pods | `200 {}` → `200 {"status":"received"}` after redeploy, same payload |
| Revert restores original behavior | `200 {}`, confirmed only after ruling out a cache false-positive |
| `helm history` | Failed revision preserved; rollback available, fix-forward chosen |

No Python changed. `values.yaml` unchanged.

**Step 10 (values-prod.yaml + issue write-ups) — done 2026-08-23. Closes Part 2.** New `docs/issues/013-config-assumes-localhost.md` (H6), `014-mock-service-containerization.md` (L6), `015-container-entrypoint-preflight.md` (M6+R4), `016-ingress-body-size-cap.md` (M2 ingress half) — every issue ID fixed in Part 2 now has a write-up, matching Part 1's standard.

**Writing `values-prod.yaml` found two real template bugs, neither hypothetical — both caught by actually rendering the file.**

1. **`mock-downstream.enabled: false` didn't do anything.** `serviceaccount.yaml` already gated on `$spec.enabled` (step 4); `mock-downstream/deployment.yaml` and `service.yaml` never did. Fixed: wrapped both in `{{- if $spec.enabled }}`. Verified: `helm template -f values-prod.yaml` now renders 2 Deployments/Services/ServiceAccounts instead of 3.
2. **An explicit per-rule `url` was silently discarded.** `configmap-rules.yaml`'s `merge (dict "url" $downstreamURL) .` put the derived URL in the merge *destination*, and Sprig's `merge` keeps the destination's value on conflict — so a rule's own `url` would always lose to the auto-derived mock-downstream one, even with mock disabled and no such Service to point at. Confirmed empirically before fixing (rendered a rule with an explicit override, got the mock URL back anyway). Fixed by swapping the merge order: `merge . (dict "url" $downstreamURL)`. Verified both ways — explicit `url` now wins when present; unmodified local `values.yaml` (no rule specifies one) renders byte-identical `rules.json` to before the fix, confirmed by redeploying to the live cluster and observing the pod-template hash **didn't change** (no spurious rollout), then a real signed request still behaved correctly.

**`values-prod.yaml` is deliberately small** — three overrides only (image registry/tag with `CHANGEME` placeholders, `mock-downstream.enabled: false`, `ingress.className: nginx` + `bodyLimit.provider: nginx`), exactly what the design promised from the start, nothing extra invented.

**One gap left honestly open, not papered over:** `values-prod.yaml` does not override `components.pokeproxy.rules` with real downstream URLs — with mock disabled and no rule specifying its own `url`, every rule still points at the now-nonexistent mock Service. The merge-order fix means the *mechanism* to override exists and is verified; the *values* don't, because inventing plausible fake production URLs would be fabrication. A real deployment reusing this chart needs to add explicit `url:` fields per rule when disabling mock.

**Not deployed live** — no production cluster exists, declared out of scope from the first design pass. Verification is `helm lint --strict` and `helm template` only.

Verified by execution:

| Check | Result |
|---|---|
| `helm lint -f values-prod.yaml --strict` | Clean |
| `helm template -f values-prod.yaml` | Mock resources correctly absent; nginx ingress annotation (`proxy-body-size: "1m"`) renders in place of the Traefik `Middleware` reference |
| Explicit per-rule `url` override | Respected after the fix, confirmed via a standalone test render |
| Local `values.yaml` behavior after the fix | Unchanged — same `rules.json`, same pod-template hash, real request still correct |

**Part 2 — Infrastructure & Deployment is complete.** All 10 steps done. H6, H1-consequence, H7 (K8s half), L6, R4, M6, and M2 (ingress half) all closed with write-ups; the remaining Part 1 NICE TO HAVE backlog (L1, L2, L5) and R2/R3 stay open in `docs/issues/000-known-gaps.md`, none blocking. Next is Part 3 — CI/CD & GitOps.

No Python changed.

---

## Part 3 — CI/CD & GitOps

**Design agreed 2026-08-23. Steps 1–5 implemented and verified live; steps 6 (rollback) and 7 (write-ups) open. Audited 2026-08-23 — see `Current State` for the findings table.** Full reasoning, alternatives, step detail and the verification/rollback model: `docs/planning/part-03-cicd-gitops.md`. Only decisions and measured results go here.

**Stack:** GitHub Actions (lint · test · build · scan · sign · promote, never touching a cluster) · GHCR with short-sha tags **and** digest pinning, SBOM + SLSA provenance, Trivy gating HIGH/CRITICAL, cosign keyless signing · Argo CD reconciling `main` into a second k3d cluster from `deploy/envs/prod/values.yaml` · a PostSync Job sending real protobuf + HMAC through Traefik and asserting the mock downstream received it · rollback by git revert via a `workflow_dispatch` workflow.

**The constraint that decided the architecture:** GitHub-hosted runners cannot reach a cluster on this laptop. Any design where CI runs `kubectl`/`helm` against the target needs a self-hosted runner here, which is CI mutating the cluster behind git's back. A pull-based agent in the cluster isn't a preference — it's the only thing that connects cloud CI to this cluster.

**Decisions that overruled my initial recommendation, all deliberate:**
- **No ephemeral CI cluster.** I proposed a throwaway k3d inside the runner as a pre-promotion gate — the strongest option, because a failing image never enters desired state. Overruled: deploy to prod and verify there. Consequence, stated plainly rather than smoothed over: **verification becomes detection, not prevention.** Exactly one failure class escapes — pods healthy but functionally wrong — for the duration of one E2E run. Crash/probe/pull failures still can't reach users (`maxUnavailable: 0`), and unrenderable charts still die in CI.
- **Second k3d cluster as the prod stand-in**, since no production cluster exists. Own context, own Argo CD, own sealing key, port 8081. Converts the whole CD half from described to demonstrated.
- **Short-sha tags** kept over my full-sha proposal, for consistency with `deploy.sh` and Part 2's artifacts. Digest pinning kept alongside after I made the case that a git sha makes a tag *unique*, not *immutable* — a build re-run against a moved `python:3.13-slim-bookworm` base (N5) republishes different bytes under the same tag, and `IfNotPresent` then leaves different nodes on different builds.
- **Argo CD Notifications auto-revert documented, not built.** A flaky E2E would otherwise become an automatic production change.

**A claim I made, then checked, then had to retract.** An intermediate draft moved env values *into* the chart directory and kept `values-prod.yaml` by rewriting it, justified by "Argo CD rejects Helm `valueFiles` resolving outside the Application's `path`." **That was wrong.** The real boundary is the **repository root**, not the app path — a single-source Application accepts `../../envs/prod/values.yaml` fine; only escaping the repo root, including via a symlink, is rejected. Multi-source with `$values` exists for values in a *different repo*, which is not our case. So the original decision stands: values move to `deploy/envs/{local,prod}/values.yaml` and `values-prod.yaml` is deleted. The dev path is unaffected — `helm -f` has never required a values file inside the chart, so `deploy.sh` and `seal-hmac.sh` each change one path.

**A trap avoided by checking rather than assuming.** Argo CD maps `helm.sh/hook: post-install,post-upgrade` to `PostSync`, and **ignores all Helm hooks entirely once any Argo hook annotation is present** — so the planned dual annotation yields exactly one execution per path, never two. More importantly: `helm.sh/hook: test` has **no Argo CD equivalent and is skipped**. `helm test` is the natural-looking idiom for "verify the release works" and it would have silently never run in prod. The plan's `post-install,post-upgrade` choice is now deliberate rather than lucky.

**Best-practice review pass (2026-08-23), six gaps folded in.** Image vulnerability scanning (Trivy, HIGH/CRITICAL, `ignore-unfixed`), SBOM + SLSA provenance from buildx, Dependabot for `github-actions` and `docker` (digest-pinned bases are unmaintainable without a bot — that trades drift for staleness, it doesn't fix it), branch protection on `main` requiring the CI checks, `ttlSecondsAfterFinished` on the E2E Job, and a bounded `retry.limit` on the Argo Application so `selfHeal` plus a persistently failing PostSync hook can't re-sync forever. Also added: cosign keyless signing via GitHub OIDC, and a `production` GitHub Environment on the promote job for the deployment audit trail.

**A real subtlety the review surfaced about rollback.** Rollback is a pure image swap — no database, no migrations, Redis is a best-effort cache the service already degrades past. **But** the cache stores downstream *responses* keyed by payload hash with `CACHE_TTL_SECONDS=300`, so a poisoned entry written by a bad version replays for up to five minutes *after* the rollback lands. Rollback fixes new payloads instantly and previously-seen ones only after the TTL. `redis-cli FLUSHALL` goes in the rollback runbook as an optional step. This is the same trap that made a Part 2 step 9 revert look like it had failed when it had actually worked.

**Corrected my own reasoning on `ruff format`.** I'd justified skipping it with "a formatting sweep would bury real history" — but `.git-blame-ignore-revs` is the standard remedy and I should have said so. Still skipping it, on the honest grounds: scope, and a 13-file formatting diff is noise in a submission a human reads.

**Verified before the plan was written, so it doesn't rest on assumptions:**

| Check | Result |
|---|---|
| Traefik Service | `traefik.kube-system`, `80:32637/TCP` — gives the E2E its in-cluster URL |
| `main` branch protection | none at design time; **being enabled** (D11), so the promote job needs a documented bot bypass |
| Argo CD `valueFiles` scope | bounded by the **repo root**, not the Application `path` — `../../envs/prod/values.yaml` is valid single-source |
| Argo CD Helm hooks | `post-install`/`post-upgrade` map to `PostSync`; any Argo hook annotation suppresses *all* Helm hooks; `helm.sh/hook: test` is skipped entirely |
| Docker server arch | `amd64` — single-platform builds |
| `git rev-parse --short HEAD` | **7 chars**, so CI's `${GITHUB_SHA:0:7}` and `deploy.sh` agree |
| Is the downstream forward synchronous? | **yes** — `proxy.py:123` awaits the forward before responding, so the E2E has no polling race |
| `ruff format --check` | would reformat **13 of 27** files → deliberately not added to CI |
| Repo visibility | PUBLIC → GHCR packages can be public, cluster pulls anonymously |

**Seven ordered steps:** (1) CI lint+test + Dependabot · (2) CI build+push to GHCR with SBOM/provenance, Trivy scan, cosign signing · (3) **E2E script, derived image, hook Job, 3 NetworkPolicy rules — proven on the existing dev cluster** · (4) prod cluster + Argo CD + the `deploy/envs/` move · (5) CI promote with the `production` Environment, measure commit→serving · (6) `rollback.yml` and all three failure scenarios executed · (7) write-ups and docs.

Step 3 precedes step 4 on purpose: it's the highest-value and highest-risk piece, it's fully provable on the cluster that already exists, and it stands alone even if the prod cluster never happens.

**Closes when implemented:** S4, N7, and the sha-drift class recorded three times in Part 2 (once the cluster can only run a digest CI published, the drift is structurally impossible).

---

## Part 4 — Observability

_Not started._

---

## Part 5 — Zero-to-Running Automation

_Not started._

---

## Final Review / Remaining Work

_Not started._
