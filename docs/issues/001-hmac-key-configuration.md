# C1 — HMAC key: wrong variable name, value never validated

**Severity:** Critical · **Wave:** 1 · **Status:** Fixed
**Files:** `app/src/pokeproxy/config.py`, `app/.env.example`, `app/README.md`

## Problem

Two defects in one configuration field.

**1. The service could not start from its own documentation.** Code reads `POKEPROXY_HMAC_KEY` (`config.py:18`); `.env.example:1` and `README.md:52` defined `POKEPROXY_SECRET`. The documented Quick Start died in lifespan with a `ValidationError`, exit 3.

**2. The value was unvalidated.** `hmac_key` was a bare `base64.b64decode()` — no length check, lax decoding:

| Value | Decodes to | Started before? |
|---|---|---|
| `changeme` (unreplaced placeholder) | 6 B garbage | yes |
| empty string | 0 B | yes |
| `YQ==` | 1 B | yes |
| `abcd efgh` | 6 B — space silently discarded | yes |

Lax `b64decode` discards non-alphabet characters instead of rejecting them, so a mangled key was silently truncated rather than flagged.

**Correction to my original review note:** it was not *completely* unvalidated. `main.py:21` reads `settings.hmac_key` during lifespan, so a base64 *padding* error did fail at startup — accidentally, as a bare `binascii.Error` naming no variable. Accurate description: no meaningful validation, plus one accidental check with a useless message.

## Production Impact

| Defect | Impact |
|---|---|
| Name mismatch | **Guaranteed CrashLoopBackOff on first deploy.** Container starts → lifespan fails → exit 3 → restart → backoff. On-call gets a 25-line pydantic traceback with the one actionable line buried near the bottom. |
| No validation | **Silent.** Service comes up green, passes probes, serves traffic — with HMAC auth effectively disabled. An empty key still produces a well-formed signature, so a client sharing the same broken config authenticates fine. No log, no metric, no failed probe distinguishes this from a correct deployment. |

## Options Considered

| Decision | Options |
|---|---|
| Which name wins | rename code to `pokeproxy_secret` · align docs to `POKEPROXY_HMAC_KEY` · accept both via `AliasChoices` · rename to `POKEPROXY_HMAC_SECRET_B64` |
| Where validation lives | `field_validator` on `Settings` · explicit check in `lifespan` · lazily in the `hmac_key` property |
| Base64 strictness | lax (current) · `validate=True` |
| Minimum key length | none · 16 B · 32 B (RFC 2104) |

## Decision

**Align docs to `POKEPROXY_HMAC_KEY`.** The name states algorithm and role; "secret" states neither. **Rejected `AliasChoices`** — aliasing is a migration tool and there is nothing to migrate; a greenfield service accepting two names for its own secret starts with debt.

**Validate in a `field_validator`** so failure happens at `Settings()` construction — before the Redis and HTTP clients build — not at first use. Strict decoding, 16-byte decoded minimum.

**16 bytes, not 32.** RFC 2104 recommends a key at least as long as the hash output (32 B for SHA-256). The committed dev secret decodes to 25 B, so a 32 B floor would reject it and force regenerating the secret in both `.env.example` and `scripts/load_generator.py` — turning a configuration fix into one that also edits the load generator. 128 bits is far beyond brute-force reach for an HMAC key, is a common industry floor, and still catches all three failure modes that matter: wrong variable name, placeholder value, empty value.

Strict decoding is the *secondary* defence — `changeme` is valid base64. **The length check does the real work.**

## Implementation

`MIN_HMAC_KEY_BYTES = 16` plus `_decode_hmac_key()`: strict decode, enforce the floor, raise `ValueError` naming the variable and giving the fix. A `field_validator` on `pokeproxy_hmac_key` calls it at construction; the `hmac_key` property calls the same helper — one source of truth. `.env.example` and the README table renamed.

Operator now sees:

```
POKEPROXY_HMAC_KEY decodes to 6 byte(s), below the 16-byte minimum.
Set POKEPROXY_HMAC_KEY to a base64-encoded key of at least 16 bytes.
Generate one with: openssl rand -base64 32
```

`scripts/load_generator.py` deliberately untouched — its 25 B default clears the 16 B floor, which is why 16 was chosen.

## Verification

All run in WSL Ubuntu against `app/.venv` (Python 3.13). Nothing projected.

| Check | Result |
|---|---|
| Suite from `app/` | **16 passed** (was 5); 11 new in `tests/test_config.py` |
| New tests vs HEAD's `config.py` | **9 failed / 2 passed** |
| Start from `.env.example` verbatim | `Application startup complete` (was exit 3) |
| `POKEPROXY_HMAC_KEY=changeme` | exit 3 + message above, 3/3 runs |
| `cp .env.example .env` flow | works; reverting to the old name fails loudly |
| `ruff check .` | clean |

The 2 tests passing at HEAD (`test_accepts_valid_key`, `test_missing_key_names_the_variable`) are guard tests, not cover for this bug — recording that rather than claiming all 11 are regression cover.

Before/after, measured on both code versions:

| Case | HEAD | Fixed |
|---|---|---|
| `changeme` | accepted, 6 B | refused |
| empty string | accepted, 0 B | refused |
| `YQ==` | accepted, 1 B | refused |
| `abcd efgh` | accepted, 6 B | refused |
| non-base64 / stripped padding | refused (raw `binascii.Error`) | refused (named message) |
| valid 25 B dev secret | accepted | accepted |

**Timing:** validation costs 0.187 s per 1000 constructions. Measured wall-clock to failure was 4.4–5.2 s, but that is module import over WSL `/mnt/c` (3.2 s for the app alone) — re-measure in-container in Part 2 rather than treating it as a property of the code.

**Incidental, verified not assumed:** the app starts with **no Redis running** (nothing on 6379, connection refused) — `aioredis.from_url` is lazy. Input to C4 and M1; not addressed here.

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| 16 B floor is below RFC 2104's 32 B for SHA-256 | Deliberate, to keep scope. Raising it = 1 line + a secret rotation, which the deployment must support anyway |
| Working secret still committed in `.env.example` and the load generator default | **L1, Wave 5.** C1 fixes name + validation; L1 fixes the value. Merging them pulls in the load generator |
| Error still delivered as a ~25-line pydantic traceback | Actionable text present but buried. Deferred to **C5**, which introduces structured logging — the right place for a clean startup-error path |
| Rename breaks any deployment setting `POKEPROXY_SECRET` | Loudly, at startup, no alias. Nothing is deployed yet, so loud is correct — and it is exactly why the alias option was rejected |
| `POKEPROXY_CONFIG` still unvalidated at startup | **H1** |
| No replay protection on signed payloads | **M3**, documented-only |
