# P5-3 — no teardown existed in code; prod had none documented at all

**Severity:** Blocker · **Part:** 5, step 6 (2026-08-25) · **Status:** Fixed
**Files:** `scripts/down.sh` (new), `scripts/down-prod.sh` (new), `Makefile`, `deploy/README.md`

## Problem

Before Part 5, `deploy/README.md`'s `## Teardown` section (line 104, pre-Part-5 revision — `git show HEAD:deploy/README.md` lines 104-109) was three lines of prose:

```bash
k3d cluster delete pokeproxy
```

with a sentence noting `.secrets/sealing-key-local.yaml` and `deploy/envs/local/values.yaml` are left on disk. That's it — a command to copy-paste by hand, not a script. There was no `tilt down` step either (Part 5's Tiltfile didn't exist yet at that point, but even the app's Helm release had no scripted uninstall path).

**Prod had nothing at all.** Searching the pre-Part-5 `deploy/README.md` for any prod-teardown mention (`grep -n -i "prod.*delete\|delete.*prod\|down-prod\|teardown"`) turns up only the one `## Teardown` heading above — zero lines addressing the `pokeproxy-prod` k3d cluster, which `docs/planning/part-03-cicd-gitops.md`'s step 4b had stood up with its own Argo CD install and its own sealing key. A reviewer or engineer who bootstrapped prod had no documented, let alone scripted, way to tear it back down.

## Production Impact

A dev-loop cluster (or a prod stand-in) that can only be *created* in code and only *destroyed* by hand-typing a command from a README is a real operability gap, not cosmetic:

- **Manual cleanup drifts from what's documented.** The README's own teardown prose can go stale the moment the create-side automation changes (which is exactly what happened here — Part 5 added a Tiltfile-managed Helm release that plain `k3d cluster delete` doesn't know to `helm uninstall` first; the prose never caught up because there was no script to catch up).
- **No repeatable path back to a known-clean state.** Without a real teardown command, verifying "does this actually bootstrap from nothing" requires manually finding and deleting the right `k3d`/`docker` resources correctly, which is itself error-prone and easy to skip under time pressure — undermining the whole point of a clean-machine check. Concretely: Part 5's own step-8 clean-machine verification (`docs/planning/part-05-automation.md`, Definition of Done item 1) depends on a real, trustworthy `make down` to establish the "no cluster" starting condition in the first place — without it, that verification would have had to start with manual `k3d`/`docker` commands of unknown correctness, which is precisely the kind of untrustworthy setup step 8 exists to avoid.
- **Prod specifically had a stronger failure mode**: an abandoned `pokeproxy-prod` cluster with a live Argo CD install and a sealed HMAC secret left running indefinitely, discoverable only by an engineer who happened to run `k3d cluster list` and recognized the name — not a scenario a real team wants to depend on for hygiene.

## Options Considered

- **Leave it as documented prose, just make it accurate.** Cheapest, but doesn't close the actual gap the assignment names: "Include a teardown path" (`README_HOME_ASSIGNMENT.md`, Part 5) reads as a requirement for something runnable, matching how the same section requires "one command" rather than one documented procedure for standing the stack up.
- **A single teardown script covering both `local` and `prod`** via an `--env` flag, mirroring `preflight.sh`'s and `seal-hmac.sh`'s pattern. Rejected: `local`'s teardown needs to talk to Tilt first (`tilt down`, to `helm uninstall` the release Tilt owns) before deleting the cluster; `prod`'s Helm release is owned by Argo CD, not Tilt, and deleting the `pokeproxy-prod` cluster removes everything in it regardless — there's no equivalent `tilt down` step to run. Forcing both into one script with an env branch would mean a conditional in the middle for a step that only exists on one side, for a marginal reduction in file count.
- **Two small scripts, one per environment, each doing exactly what that environment's lifecycle needs.** Chosen — see Decision.

## Decision

`scripts/down.sh` for `local`: `tilt down` (uninstalls what Tilt deployed, including the real Helm release) then `k3d cluster delete pokeproxy`. `scripts/down-prod.sh` for `pokeproxy-prod`: just `k3d cluster delete pokeproxy-prod` — no `tilt down` step, since prod was never a Tilt-managed cluster (D9, `docs/planning/part-05-automation.md`: Tilt is dev-only, prod stays Argo CD) and deleting the cluster removes the Argo CD install and every workload in it regardless of which controller put them there. Both wired into `Makefile` (`make down`, `make down-prod`) as one-line delegates, matching every other target's pattern — no logic lives in the Makefile itself.

`tilt down`'s failure inside `down.sh` is deliberately **non-fatal**: if Tilt itself is in a bad state (stale lock file, engine already dead, etc.), the script prints a warning and proceeds straight to `k3d cluster delete` anyway, rather than aborting and leaving a stuck cluster the operator now has to clean up by hand — the exact failure mode this issue exists to close. `k3d cluster delete` removes every workload in the cluster unconditionally, so a failed `tilt down` doesn't leave anything undeleted; it only means Tilt's own release bookkeeping (e.g. `helm list` reporting the release as still "deployed" from Tilt's perspective, a cosmetic state that dies with the cluster a few seconds later) wasn't cleanly closed out first.

Neither script deletes `.secrets/`. Both are explicit about this in their own output — `down.sh` inherits it implicitly (never touches the directory), `down-prod.sh` prints it directly: `.secrets/sealing-key-prod.yaml` is the only backup of the prod key, and this script never touches it.

## Implementation

- `scripts/down.sh` (new, 17 lines): guards on the cluster actually existing (`k3d cluster list pokeproxy`) before doing anything, so a repeat run against an already-torn-down state is a clean no-op rather than an error; `tilt down --context "$KUBE_CONTEXT" || echo "..."` for the non-fatal behavior described above; then `k3d cluster delete pokeproxy`.
- `scripts/down-prod.sh` (new, 15 lines): same existence guard against `pokeproxy-prod`, then `k3d cluster delete`, then the `.secrets/sealing-key-prod.yaml` reminder printed directly in the script's own output rather than left only in a README someone might not read.
- `Makefile`: `down: bash scripts/down.sh`, `down-prod: bash scripts/down-prod.sh` — one line each, no inline logic.
- `deploy/README.md`: the stale three-line `## Teardown` prose replaced — local points at `make down`/`bash scripts/down.sh` with what it actually does underneath; prod gained its own `### Teardown` subsection (previously nonexistent) pointing at `make down-prod`/`bash scripts/down-prod.sh`.

## Verification

**Local (`down.sh`), live this session (2026-08-25, Part 5 step 8's clean-machine verification):**

| Check | Result |
|---|---|
| `make down` on a live cluster | exit 0 |
| `docker ps` immediately after | zero `pokeproxy`/`k3d-pokeproxy-*` containers |
| Final `make down` after the full step-8 cycle | exit 0, ~16s wall clock |
| `docker ps -a` / `k3d cluster list` after | both confirmed empty |
| `.secrets/` after teardown | both sealing keys still present, untouched by the script |

**Prod (`down-prod.sh`):** not re-run in this session — Part 5's headline path is local (D6/D9, `docs/planning/part-05-automation.md`), and step 8 scoped its clean-machine verification to `local` accordingly. The existing live record is from step 7's own verification the same day: a full `make up-prod`/`down-prod` cycle (Argo Synced/Healthy, ingress `401`, prod cluster deleted, sealing key preserved), and `deploy/README.md`'s own Teardown subsection records `make_down_prod_exit=0, cluster deleted, .secrets/sealing-key-prod.yaml left untouched` from that same run. Not independently re-verified by me this session — stating that plainly rather than implying I re-ran it.

## Tradeoffs / Remaining Risk

- **`tilt down`'s failure is swallowed, not surfaced as a script failure.** This is a deliberate choice (see Decision) — a stuck Tilt engine should never block cluster deletion, since the cluster deletion is what actually guarantees a clean state. The tradeoff: if `tilt down` fails for a reason worth investigating (not just a stale lock), the operator only sees a one-line warning in the middle of otherwise-normal output, not a hard stop demanding attention. Acceptable here because `k3d cluster delete` is the actual source of truth for "is this torn down," verified independently in every teardown check above.
- **Neither script backs up or removes `.secrets/`.** This is intentional, not an oversight — deleting a sealing key is a one-way, high-consequence action (`docs/issues/023`), and a teardown script's job is cluster lifecycle, not secret lifecycle. Recorded here so it isn't mistaken for a gap in coverage.
- **Prod teardown's verification is one day older and from a different working session than local's.** Documented plainly above rather than folded into a single "verified" claim that would overstate how current the prod evidence is.
