# F-2 — `seal-hmac.sh` silently minted a fresh sealing key, incompatible with GitOps by construction

**Severity:** Blocker · **Part:** 3 requirement audit (2026-08-23) · **Status:** Fixed
**Files:** `scripts/seal-hmac.sh`, `scripts/init-sealing-key.sh` (new), `deploy/README.md`

## Problem

`seal-hmac.sh`'s original design treated a missing sealing key as a convenience case: if `.secrets/sealing-key-$ENV.yaml` didn't exist, it minted a new RSA keypair on the spot, installed the Sealed Secrets controller with it, and unconditionally re-sealed the target values file against the new key. This was a reasonable default for `local`, where Helm reads the same working-tree file the script just wrote — sealing and consuming happen on the same machine, in the same step.

It is not reasonable for `prod`. Argo CD does not read the local working tree; it reads `deploy/envs/prod/values.yaml` as committed to GitHub. A fresh clone running `seal-hmac.sh --env prod` would mint a brand-new key, re-seal the *local copy* of the values file against it, and stop — the ciphertext already committed to git, encrypted against whichever key produced it originally, is untouched on GitHub and now undecryptable by anything, since the key that could decrypt it was never persisted anywhere retrievable in the first place (`.secrets/` is deliberately gitignored).

The failure is silent and structurally deferred: nothing errors at sealing time. It only surfaces ~600 seconds later, when `bootstrap-prod.sh`'s converge loop times out because the SealedSecret can never decrypt, `pokeproxy-hmac` is never created, and the pods sit in `CreateContainerConfigError`.

## Production Impact

Anyone reproducing this repo's GitOps demo from a fresh clone — a reviewer, a new engineer, or Part 5's clean-machine one-command bootstrap — hits a cluster that never converges, with the actual cause (key/ciphertext mismatch) buried behind a generic timeout and a Kubernetes-level symptom that looks like a probe or image problem, not a secrets problem. This is the same class of defect as `docs/issues/017-sealed-secret-key-portability.md` (B1, Part 2), but worse: B1's fix (re-seal unconditionally when a key is freshly generated) is exactly the mechanism that causes *this* failure under GitOps, because Part 2 never had a second party (Argo, reading from git) that could disagree with the local working tree about which ciphertext is current.

## Options Considered

- **Patch the guard**: detect "key missing but committed ciphertext already looks real" and warn. Narrower fix, but still allows a silent key mint to proceed by default; the operator has to notice a warning rather than being stopped.
- **Auto-commit and push the re-seal from `bootstrap-prod.sh`**: fully automates recovery, but turns a bootstrap script into something that writes to `main` on every run — a new standing capability with its own blast radius, on the same repo where the branch-protection/PAT episode (`docs/issues/024-branch-protection-pat.md`) already showed how much friction a bot push to `main` can cause.
- **Commit the public cert half**, so sealing works offline without a live cluster holding the private key. A bigger structural change than this fix warrants — it changes how sealing works for both environments, not just the fresh-clone edge case.
- **Split provisioning from sealing, and stop minting silently.** Treat the sealing key as infrastructure state that is provisioned once, deliberately, by a human, and never regenerated as a side effect of another script's convenience path.

## Decision

The last option. `scripts/init-sealing-key.sh --env {local,prod}` is now the only place a sealing key is ever generated: a one-time, explicitly human-run step that refuses to run a second time against an existing key file, and prints a hard, explicit instruction to back the generated file up (password manager, encrypted vault — operator's choice) before doing anything else, since it is gitignored by design and this is the only chance to preserve it. `seal-hmac.sh` lost its key-generation path entirely. If the key file is missing, it now exits 1 immediately with the exact remediation (`init-sealing-key.sh`, or restore your backup) instead of proceeding.

This doesn't make key loss recoverable — losing the key still permanently undecrypts every ciphertext sealed against it, same as before. What it fixes is the silent, automatic path to that state: the failure now happens at the moment the actual precondition (a real key) is missing, with a clear message, instead of 600 seconds later as an opaque timeout with a completely different symptom.

## Implementation

- `scripts/init-sealing-key.sh` (new): generates the RSA keypair via the same `openssl req` + `kubectl create secret --dry-run` pipeline the old `generate_sealing_key()` used, refuses to overwrite an existing key file, prints the backup reminder and the disaster-recovery procedure (rotate = delete the key, re-run this script, re-seal, commit and push yourself — a deliberate manual act, not automated).
- `scripts/seal-hmac.sh`: `generate_sealing_key()` removed; the missing-key branch now prints the remediation and `exit 1`. The now-dead `key_freshly_generated` branch in the reseal-guard logic was removed along with it — `already_sealed()` alone now decides whether to reseal.
- `scripts/deploy.sh`, `scripts/bootstrap-prod.sh`: dropped the `openssl` `require_command` check — neither script generates keys directly anymore, and `seal-hmac.sh` no longer needs it either.
- `deploy/README.md`: new "step 0" documenting the one-time provisioning step, ahead of the existing seal-hmac.sh step.

## Verification

Live, isolated, and reversible — see `WORKLOG.md` ("F-2 regression test, isolated and reversible"):

| Step | Result |
|---|---|
| Move `.secrets/sealing-key-local.yaml` aside | file confirmed absent |
| Run `seal-hmac.sh --env local` | printed the new fail-loud message and **stopped** — no controller install, no reseal attempted |
| `deploy/envs/local/values.yaml` after the failed run | `git status` shows no diff — confirmed untouched |
| Restore the key, re-run normal deploy flow | dev cluster deployed cleanly, E2E passed |
| Full from-scratch prod bootstrap with the real (pre-existing) key | `Using sealing key at .../sealing-key-prod.yaml` — correct key reused, no silent mint, Synced/Healthy, E2E passed |

Note: the isolated negative test's own `$?` reported `0` through this session's specific `wsl.exe`-piped invocation path — traced to a tool-chain artifact independent of this script (even a bare `false; echo $?` misreports `0` through the same path, while native Git Bash reports it correctly) — so the result above was confirmed from actual side effects (log output, file state), not from the reported exit code.

## Tradeoffs / Remaining Risk

This does not solve secret **backup** — it only removes the silent, automatic path to an unrecoverable mismatch. The operator is still responsible for actually backing up the printed key file; nothing enforces that they did. A real production deployment of this pattern would put the sealing key in a proper secret manager (Vault, a cloud KMS-backed store) with its own backup and rotation story rather than relying on an operator's own discipline — noted here rather than built, consistent with this repo's existing practice of documenting the real answer without implementing infrastructure the assignment's scope doesn't warrant (see the Argo CD Notifications auto-revert decision in `docs/planning/part-03-cicd-gitops.md`).
