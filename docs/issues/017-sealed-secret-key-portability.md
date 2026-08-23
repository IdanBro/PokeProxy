# B1 — Committed sealed-secret ciphertext didn't survive a fresh clone

**Severity:** Blocker · **Part:** 2 audit (2026-08-23) · **Status:** Fixed, mechanism superseded — see below
**Files:** `scripts/seal-hmac.sh`

> **Superseded, 2026-08-23 (`docs/issues/023-sealing-key-silent-generation.md`).** The fix below — re-seal unconditionally whenever `generate_sealing_key()` ran — was correct for Part 2's single-machine dev flow, but became the direct mechanism of a worse, GitOps-specific failure once a second party (Argo CD, reading from git) could disagree with the local working tree about which ciphertext is current. `generate_sealing_key()` no longer exists in `seal-hmac.sh`; key provisioning moved to a separate, explicit, one-time script (`scripts/init-sealing-key.sh`), and a missing key now fails loudly instead of triggering an automatic re-seal. This document is kept for the historical record of what shipped and why at the time.

## Problem

`deploy/helm/pokeproxy/values-local.yaml` commits `hmac.encryptedValue`, ciphertext produced by `kubeseal` against the Sealed Secrets controller's RSA key. That key is generated once by `scripts/seal-hmac.sh`'s `generate_sealing_key()` and written to `.secrets/sealing-key.yaml`, which `.gitignore` correctly excludes from the repo (it's a private key).

`seal-hmac.sh` only re-seals when `values-local.yaml` is still `CHANGEME`. On any machine where the file already holds ciphertext — which is true for every fresh clone, since the value is committed — the script skipped sealing entirely, regardless of whether the sealing key on that machine was the same one the ciphertext was originally encrypted against.

On a fresh clone, `.secrets/sealing-key.yaml` doesn't exist either (never committed), so the script's other branch fires: it generates a **new, random** RSA keypair and installs the controller with it. The committed ciphertext, encrypted against the *old* key from whichever machine produced it, can no longer be decrypted by the new key.

## Production Impact

A new engineer, or a CI runner, cloning the repo and running the documented deploy sequence gets: the controller logs `no key could decrypt secret (POKEPROXY_HMAC_KEY)`, no Kubernetes `Secret` is created, and `pokeproxy`'s pods sit `Pending`/`CreateContainerConfigError` waiting on a `secretRef` that will never resolve. Nothing in the failure points at the actual cause — the visible symptom is a missing Secret, not "you have the wrong sealing key." This blocks Part 3's CD (any pipeline redeploying from a clean checkout hits it) and Part 5's one-command bootstrap (the entire point of which is working from a clean machine).

Not caught earlier because step 6's verification — delete and recreate the k3d cluster, confirm the same ciphertext still decrypts — passed for the wrong reason: `.secrets/sealing-key.yaml` survived on disk across that recreate. It never tested the case where the key file is also gone, which is what a fresh clone actually is.

## Solution

Track whether `generate_sealing_key()` actually ran in this invocation (`key_freshly_generated`). If it did, `seal-hmac.sh` now re-seals `values-local.yaml` unconditionally, overwriting whatever ciphertext is already committed there. If the key was reused (the normal case on a developer's own machine across repeated runs), the existing skip-if-already-sealed behavior is unchanged — the script stays idempotent and doesn't rewrite a file that's already correct.

Considered and rejected: committing the sealing controller's public certificate so `kubeseal --cert` could seal offline without a live controller. That only solves the problem if the *private* key is also pinned/reproducible across machines — which it isn't by design here, and making it so means committing key material to git. For this repo (a local k3d demo protecting a value that's already public in `.env.example`), that trade isn't worth it; re-sealing per-machine is the smaller, more honest fix.

## Verification

Simulated a fresh clone against the live cluster: backed up the current `.secrets/sealing-key.yaml` and `values-local.yaml`, deleted the key file, and re-ran `scripts/seal-hmac.sh`.

| Step | Result |
|---|---|
| Script output | `Generating sealed-secrets sealing key ...` → `Sealing key was freshly generated — re-sealing ... regardless of its current contents` → new ciphertext written |
| New ciphertext differs from the old | Confirmed (`AgAszJ+s...` vs. the original `AgAxbv4W...`) |
| `helm upgrade --install --atomic` with the new ciphertext | Revision 9, succeeded, 4/4 pods, 0 restarts |
| Decrypted `Secret/pokeproxy-hmac` | `dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg==` — the correct dev key, byte-for-byte |
| Second run, same machine, key unchanged | `... already holds a value sealed against the existing key, leaving it as-is` — ciphertext untouched, confirming idempotency wasn't broken |
| Cleanup | Restored the original `.secrets/sealing-key.yaml` and `values-local.yaml`, reinstalled the controller with the original key, redeployed — revision 10, 4/4 pods, 0 restarts, tree back to its pre-experiment state |
