# F-18 — `main`'s branch protection blocked the promote job's push; `GITHUB_TOKEN` and a ruleset bypass both turned out to be dead ends

**Severity:** Nice to have (write-up gap; the underlying block was a Should Fix / operational blocker at the time) · **Part:** 3 step 5 (2026-08-23) · **Status:** Fixed
**Files:** `.github/workflows/ci.yml` (`promote` job), `.github/workflows/rollback.yml`, GitHub repo secret `PROMOTE_PUSH_TOKEN`

## Problem

The `promote` job needs to push a commit directly to `main` — that's the entire mechanism by which CI updates GitOps desired state. The step 5 plan was written and reviewed when `main`'s branch protection required no status checks and no reviews (`gh api repos/.../branches/main/protection` showed `required_status_checks: null`), so the plan explicitly said "no bypass needed yet."

Between writing that plan and pushing the implementation, `main` gained `required_pull_request_reviews` (present even at 0 required approvals — its mere *presence*, not the approval count, is what gates a push) and `required_status_checks.strict: true`. The first live `promote` push failed immediately: `GH006: Changes must be made through a pull request`. `enforce_admins: false` did not help — that setting only exempts a *human* pushing with their own admin-authenticated credentials, never a workflow's `GITHUB_TOKEN`.

## Production Impact

Without a fix, every merge to `main` would build, scan, sign, and then fail at the last step — the desired-state commit that actually matters for GitOps never lands, silently turning the pipeline into "everything except the part that deploys anything." Worse, discovering this live (rather than in review) meant the first read of the actual constraint came from a failed production-adjacent job, not a design review.

## Options Considered

1. **A repo Ruleset with an `Integration`-type bypass actor for the `github-actions` App.** The natural-looking GitHub-native answer — confirmed the app's identity via `gh api apps/github-actions` (id `15368`) and attempted to register it as a bypass actor. GitHub's own validation rejected it outright: `"Actor GitHub Actions integration must be part of the ruleset source or owner organization"`. This repo is user-owned, not org-owned, and App-type ruleset bypass actors require org context a personal repo structurally cannot provide. Confirmed by reading the actual 422 response, not by assuming rulesets would work — a hard platform limitation, not a config mistake.
2. **`promote` opens and self-merges a PR** via the API, viable since 0 approvals were required. Technically sound and considered seriously. Rejected on a stated design preference: the promote commit should land on `main` directly as a single, auditable commit, not as a merge commit produced by an API-driven PR — the latter adds an extra commit and a review-adjacent artifact for a mechanical, non-reviewed change.
3. **A fine-grained personal access token**, scoped to only this repo with `Contents: Read and write`, fed into `actions/checkout`'s `token:` input so the later `git push` authenticates as an actual repo admin. `enforce_admins: false` exempts *this* from the PR requirement, the same mechanism `gh pr merge --admin` relies on.

## Decision

Option 3. Minted a fine-grained PAT scoped to this repository only (not a classic PAT, which would reach every repo on the account), stored as the `PROMOTE_PUSH_TOKEN` repository secret. `actions/checkout`'s `token:` input swaps it in for the job's git credentials before the commit-and-push step.

This surfaced a second, independent bug in the job's own reasoning, caught by re-reading the code rather than by another failure: the original `promote` job's comment claimed "no workflow recursion" because GitHub suppresses `push`-event retriggering for the default `GITHUB_TOKEN`. That guarantee is specific to `GITHUB_TOKEN` — it does not extend to a PAT-authenticated push. Switching to a PAT without also addressing this would have caused `promote`'s own push to retrigger CI, which would build, promote again, and repeat indefinitely. Fixed by adding `[skip ci]` to the promote (and later, rollback) commit subject — the one loop-prevention mechanism GitHub honors regardless of which credential authored the push.

## Implementation

`ci.yml`'s `promote` job: `actions/checkout` gained `with: { ref: main, token: ${{ secrets.PROMOTE_PUSH_TOKEN }} }`; the commit step's subject line carries `[skip ci]`. `rollback.yml`, written later in the same session, reuses the identical pattern (same secret, same `[skip ci]` convention) since it needs the exact same push capability for the same reason.

## Verification

Verified live via PR #4 (`WORKLOG.md`, "Step 5 verified live"):

| Check | Result |
|---|---|
| Promote commit lands on `main` directly | Confirmed — no PR, no merge commit |
| No second CI run from the PAT push | Confirmed against the actual GitHub Actions run list — one run only |
| Digest in git matches the running prod pod | Byte-for-byte match, `kubectl get pod -o jsonpath` vs. the committed values file |

Reused successfully for `rollback.yml` in the same session (`docs/planning/part-03-cicd-gitops.md`'s "Requirement audit", scenario C): commit landed direct on `main`, all six digests matched, and `[skip ci]` again suppressed a second run.

## Tradeoffs / Remaining Risk

The PAT is a long-lived credential with no rotation built in — smaller blast radius than a classic PAT (scoped to one repo instead of the whole account), but still a standing secret that doesn't expire or rotate on its own. A real production setup would replace it with a GitHub App installation token, which is short-lived and scoped per-installation rather than per-operator. Noted as a known gap rather than fixed, since building a GitHub App is out of scope for what this pipeline needs to demonstrate.

A second, unrelated GitHub-side anomaly surfaced in the same window and is tracked separately rather than folded in here: `PR #4`'s `opened` event never fired a CI run (three independent checks agreed it never ran), and later in the session `PR #7`'s `opened` **and** `reopened` events both failed the same way, plus a `workflow_dispatch` run's checks failed to attach to the PR's own status-check rollup even though they passed on the commit directly. Worked around each time with `workflow_dispatch` and, when that check-attachment gap mattered for merging, an admin-bypass merge. Not root-caused — most plausible explanation remains a transient GitHub-side rate limit or anti-abuse cooldown from this repo's unusually high run volume in short windows, given a personal-tier account.
