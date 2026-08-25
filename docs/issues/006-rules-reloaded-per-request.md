# H1 — Rules file re-read, re-parsed, re-validated from disk on every request

**Severity:** High · **Wave:** 3 · **Status:** Fixed
**Files:** `app/src/pokeproxy/main.py`, `app/src/pokeproxy/proxy.py`

## Problem

`stream()` called `load_rules(request.app.state.config_path)` on every single request — opening `config/rules.json`, parsing it, and re-validating every rule, synchronously, on the event loop, before routing could happen.

## Production Impact

Two distinct problems from one line:

1. **Cost paid on every request** — disk I/O and JSON parsing that only needs to happen once, blocking the event loop and potentially stalling other concurrent requests while it runs.
2. **Wrong failure moment** — a broken `rules.json` (bad JSON, invalid URL, malformed condition) was invisible at deploy time. The app would start successfully and only fail once the first request arrived, turning a config mistake into an unhandled crash on live traffic instead of a clean startup failure.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Reload strategy | load once at startup · explicit safe reload (mtime/SIGHUP, swap atomically) | **load once at startup** |
| Failure handling | keep failing at request time · fail startup like `_load_settings` (C1) | **fail startup** |

## Decision

**Startup-only.** Simpler than a safe hot-reload, turns a config error into a fail-fast startup failure instead of a request-time 500, and "a rules change is a rollout" is an honest GitOps story for Part 3. Hot-reload would also need to be proven correct in the Part 5 E2E for a feature nobody asked for. Full reasoning in `docs/planning/part-01-production-hardening.md`.

**Tradeoff accepted:** rules stop being live. Editing `config/rules.json` while the process is running does nothing until restart.

## Implementation

`main.py` gains `_load_rules(config_path)`, mirroring `_load_settings`: calls `load_rules`, and on `OSError` or `ValueError` (covers a missing file, malformed JSON, and every validation error `load_rules` itself raises) logs one `CRITICAL` line naming the config path and the error, then `raise SystemExit(1)`. `lifespan()` calls it once and stores the result on `app.state.rules`, replacing the now-unused `app.state.config_path`.

`proxy.py` no longer imports or calls `load_rules` at all — `stream()` reads `request.app.state.rules` directly.

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13).

| Check | Result |
|---|---|
| New tests (`test_startup.py`) | **6 passed** — `app.state.rules` populated at startup; `load_rules` called exactly once across 3 requests (patched and counted); invalid rule, malformed JSON, and missing file each raise `SystemExit` from `_load_rules`; the failure is logged at `CRITICAL` naming the reason |
| Full suite | **62 passed** (was 56) |
| `ruff check .` | clean |

The call-count test is the direct regression cover: it patches `main.load_rules`, issues 3 requests through `TestClient`, and asserts the loader ran once — proving the file is read at startup and reused, not re-read per request.

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| Rules are no longer live-reloadable | Deliberate (see Decision). A rules ConfigMap change needs a pod-template checksum annotation or an operator like Reloader to actually roll — flagged here for Part 2, later closed via the `checksum/config-rules` pod-template annotation (`docs/issues/013-config-assumes-localhost.md`) |
| M4 (dedup cache) interacts with this: a payload inside the dedup window is suppressed before routing, so a rules change doesn't affect it until the TTL expires, even after the restart this fix requires | Open — see `docs/planning/part-01-production-hardening.md`'s H1/M4 interaction note |
