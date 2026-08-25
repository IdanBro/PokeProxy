# P5-1 / P5-2 — a clean clone could not bootstrap the local sealing key at all, and provisioning one first didn't fix it either

**Severity:** Blocker · **Part:** 5, steps 2-3 (2026-08-25) · **Status:** Fixed (local); prod unaffected by design
**Files:** `scripts/seal-hmac.sh`, `Tiltfile` (`sealing-key` resource)

## Problem

Two symptoms, one root cause, written up together rather than as two artificially separate issues.

**P5-1 — a clean clone could not run the old `deploy.sh` (or anything downstream of `seal-hmac.sh`) at all.** `.secrets/` is gitignored (`.gitignore:1`); `seal-hmac.sh`'s pre-Part-5 missing-key branch (post-`docs/issues/023`) printed the remediation and `exit 1` unconditionally for every environment, `local` included. Correct behavior for `prod` (git is the source of truth there — see `docs/issues/023`), but for `local` it meant a fresh clone had no path forward without a manual, undocumented `init-sealing-key.sh` step before the "one command" could run at all — directly contradicting Part 5's own requirement that a clean machine reach a running stack with no manual intervention.

**P5-2 — running `init-sealing-key.sh` first didn't fix it, because `already_sealed()` would then silently skip re-sealing.** `already_sealed()` (`seal-hmac.sh:65-70`, pre-fix) only tested "the `encryptedValue:` line is non-empty and not the literal placeholder `CHANGEME`." The ciphertext already committed to `deploy/envs/local/values.yaml` satisfies that test regardless of which key produced it. So: provision a fresh key → `already_sealed()` sees real-looking ciphertext already in the file → skips re-sealing → the freshly minted keypair can never decrypt ciphertext sealed against a different (previous, or nonexistent-on-this-machine) key → `pokeproxy-hmac` Secret decrypts to nothing → `CreateContainerConfigError` → `--atomic` rolls the release back roughly 3 minutes later. The symptom at that point is a generic Kubernetes container-config failure; nothing in the failure path names "secrets" or "sealing key," so the actual cause is invisible without already knowing this history.

This is the same defect family as `docs/issues/023` (F-2), scoped to `local` instead of `prod`, and **is F-15** (`docs/planning/part-03-cicd-gitops.md`, opened 2026-08-23, nice-to-have at the time): "`already_sealed()` never confirms the ciphertext decrypts under the reused key." Filed together here because the fix for both symptoms is the same one-line change to the same function.

## Production Impact

Directly blocks Part 5's headline deliverable: "a new engineer (or CI runner) can go from a clean machine to a fully running, monitored deployment with a single entry point." Without a fix, `make up` on an actual clean clone fails one of two ways — immediately with P5-1's `exit 1` (if no key was ever provisioned), or ~3 minutes into a Helm `--atomic` wait with P5-2's opaque `CreateContainerConfigError` (if a key was provisioned but happens to mismatch, e.g. after `docs/issues/023` split provisioning out as a separate manual step). Either way, "one command" is false advertising for anyone who hasn't already run this repo before.

## Options Considered

- **Document the manual `init-sealing-key.sh --env local` step as a prerequisite.** Technically closes P5-1, does nothing for P5-2, and reintroduces exactly the two-script ordering-only-in-a-README problem Part 5 exists to remove (P5-4).
- **Make `already_sealed()` actually attempt a decrypt** (e.g., `kubeseal --recover`, or round-trip the current ciphertext through `kubeseal --raw --scope strict` decode) before trusting it. Closes P5-2 fully, including the narrower stale-but-present-key case F-15 also describes. Rejected for `local` specifically because it's strictly more machinery than the problem needs there — see Decision.
- **Mint if absent, and unconditionally re-seal only in the same run that minted.** Closes the P5-1/P5-2 scenario (the actual clean-clone path Part 5 needs) with a one-line guard, no decrypt-probing logic. Doesn't address a key that's present but simply wrong and wasn't minted this run — narrower than the ideal fix, tracked below.

## Decision

Third option, for `local` only. Sealing is RSA: the ciphertext committed to `deploy/envs/local/values.yaml` and the private key in `.secrets/sealing-key-local.yaml` are gitignored-separate by design, and a freshly minted keypair mathematically cannot decrypt ciphertext produced by any other keypair's public half. The mismatch only exists when a key was just minted — so `MINTED_THIS_RUN == true → always reseal` removes the mismatch outright, with the same one-run scope the existing mint step already has. Real decrypt-verification (the "actually more correct" option above) was rejected as unnecessary machinery for local: Helm reads the same working-tree file this script just wrote, in the same process, so the moment the key is minted, the reseal happens in the same script invocation with certainty — there's no window for a decrypt-verification step to catch anything the mint flag doesn't already guarantee. Prod is untouched, deliberately: it keeps `docs/issues/023`'s single manual `init-sealing-key.sh` step because Argo reads git, not the working tree, so re-sealing automatically on every bootstrap would mean the automation pushes to `main`, evaluated and rejected there already.

## Implementation

`scripts/seal-hmac.sh`:
- Missing-key branch is now environment-conditional: `prod` keeps the exact `docs/issues/023` behavior (`exit 1`, remediation, no mint). `local` mints via `init-sealing-key.sh` and sets `MINTED_THIS_RUN=true`.
- The reseal gate (previously `already_sealed() → skip`, unconditionally) is now: `MINTED_THIS_RUN == true → always reseal`, `elif already_sealed() → skip`. The `prod` branch structurally cannot set `MINTED_THIS_RUN` (it's only assigned inside the `local`-only mint block), so `--env prod`'s gate reduces to exactly its pre-existing logic — no regression of the F-2 fix, verified by reading the diff, not assumed.
- `Tiltfile`'s `sealing-key` `local_resource` runs this script as the first resource in the graph (`pokeproxy-helm` depends on it), so `make up`/`tilt ci` exercises this path on every run with no separate invocation needed.

## Verification

Fresh-clone simulation, run live this session (2026-08-25) as part of Part 5 step 8's clean-machine verification:

| Step | Result |
|---|---|
| `.secrets/sealing-key-local.yaml` moved aside (backed up first) | file confirmed absent |
| `make down` on the pre-existing cluster, then cold `make up` | `sealing-key` resource log: `No sealing key found at .../sealing-key-local.yaml` → `Minting one for 'local' (P5-1/P5-2, ... D5): ... a fresh key here is safe as long as we re-seal against it in the same run.` |
| Reseal actually happened | `deploy/envs/local/values.yaml` shows a real diff after the run (new ciphertext), not left untouched |
| `--atomic` did not roll back | `helm history` shows a clean install, app pods `Running`, no `CreateContainerConfigError` |
| Second `make up` immediately after (key now present, not minted) | no reseal — `values.yaml` unchanged, confirming the `elif already_sealed()` branch still short-circuits correctly on a normal rerun |

(Exact command transcripts and hashes are in `WORKLOG.md`'s Part 5 "Step 8" entry.)

## Tradeoffs / Remaining Risk

**This does not fully close F-15.** It closes the specific manifestation Part 5 needs (a clean clone with no key at all, or one freshly provisioned this run). The narrower case F-15's original text names — a key file is *present*, is the *wrong* key (e.g., a stale backup restored from a different environment, or copied between machines), and was **not** minted in this run — is unchanged: `already_sealed()` still only checks "non-empty and ≠ CHANGEME," not decryptability, so that case still silently skips re-sealing and would still fail ~3 minutes later with the same opaque `CreateContainerConfigError`. A real fix for that residual case needs the decrypt-verification option rejected above (or a hash/fingerprint check comparing the sealed key's public half against a value recorded at seal time), and is out of scope here since it isn't Part 5's actual blocker — recorded as a known, narrower gap rather than closed by implication.

Prod's manual key step (`docs/issues/023`) is unchanged and was not re-litigated here.
