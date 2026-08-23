# N7 — `seal-hmac.sh` rewrote its target values file wholesale, discarding everything else in it

**Severity:** Nice to have · **Part:** 2 audit (2026-08-23), closed in Part 3 step 4a · **Status:** Fixed
**Files:** `scripts/seal-hmac.sh`

## Problem

`scripts/seal-hmac.sh:96` (pre-fix) wrote the sealed HMAC ciphertext with `cat > "$VALUES_LOCAL" <<EOF ... EOF` — a wholesale overwrite of the entire values file, containing only the `hmac:` block. This was inert through Part 2 because `values-local.yaml` held nothing else worth preserving at the time the script ran.

It stopped being inert the moment Part 3 needed the same values file to also hold ingress, NetworkPolicy, e2e, and (in prod) six CI-owned image tag/digest fields (`docs/issues/021-values-prod-undeployable.md`'s replacement file). A wholesale rewrite would have silently deleted all of it on the next `seal-hmac.sh` run.

## Production Impact

Any operator or CI job re-running the sealing step against a values file that had accumulated other configuration would lose that configuration without warning — no error, no diff shown, just a smaller file. For a values file CI's `promote` job writes to on every merge, this would have meant a routine re-seal silently reverting the promoted image references back to whatever the sealing script happened to emit.

## Options Considered

- **Merge with `yq`**: the natural tool for a targeted YAML key update. Rejected for the *local* script specifically: `apt-cache policy yq` is empty on this box (Ubuntu 22.04; `yq` only entered the Ubuntu archive at 23.04), so depending on it here means pulling a GitHub-release binary and adding a `require_command yq` that Part 5's clean-machine bootstrap can't satisfy from a package manager alone.
- **`sed` targeted replacement**: narrower tooling requirement, works with what every target machine already has.

## Decision

Replaced the wholesale `cat >` with `write_encrypted_value()`: a `sed -i -E` substitution that replaces the `encryptedValue:` line in place when the key exists, or appends a fresh `hmac:` block when it doesn't — followed by a `grep -qF` assertion that the value actually landed, refusing to continue otherwise. `sed` is safe here for a specific, checked reason: base64's alphabet (`A-Za-z0-9+/=`) contains none of `sed`'s substitution delimiters or backreference syntax (`|`, `&`, `\`), so neither the line being matched nor the ciphertext being substituted in can be misparsed. CI's `promote` job, which runs on `ubuntu-latest` and has `yq` preinstalled, uses `yq` for its own six-field write — the two scripts deliberately use different tools for the same class of problem, justified by what's actually available on each target.

## Implementation

`scripts/seal-hmac.sh`: `write_encrypted_value(file, value)` replaces the old `cat >` block. Also gained a real bug fix surfaced by testing this change rather than by design: the "already sealed" guard tested `! grep -q "encryptedValue: CHANGEME"`, so a values file with **no** `encryptedValue` key at all contained no `CHANGEME` string either, was read as already-sealed, and was silently skipped — leaving a fresh values file with no HMAC secret. Replaced with `already_sealed()`, which requires the key to be present, non-empty, and not the literal placeholder.

## Verification

| Guard case | Expected | Result |
|---|---|---|
| Existing key, other content in file | re-seals, everything else survives | Pass — sentinel tag *and* digest fields intact after reseal |
| No `hmac` key at all | appends without loss | Pass |
| Already sealed, key unchanged | file untouched | Pass — sha256 identical before/after |
| `encryptedValue:` present but **empty** | treated as unsealed, re-seals | Pass (this is the bug this change surfaced and fixed) |
| `encryptedValue: CHANGEME` | re-seals | Pass |
| `--env staging` (invalid) | rejected | Pass |

Live: render before vs. after the `deploy/envs/` restructuring came back byte-identical; `helm lint --strict` clean; `deploy.sh` green at revision 11, E2E post-upgrade hook passed.

## Tradeoffs / Remaining Risk

`sed`'s safety here depends on the specific fact that base64 output never contains a `sed` metacharacter — this is checked and true for this use, not a general-purpose YAML-editing solution. If this script ever needs to write a value that isn't base64 (or anything with `|`, `&`, or `\`), this approach needs revisiting. This class of tooling gap (targeted-edit script relying on `sed` because `yq` isn't reliably available) is itself a symptom of Part 5's "clean machine" requirement not yet being formalized against a fixed base-image contract.
