# C2 — Unbounded forward retry: no cap, no deadline, a leaked client per attempt

**Severity:** Critical · **Wave:** 3 · **Status:** Fixed
**Files:** `app/src/pokeproxy/proxy.py`, `main.py`, `config.py`

## Problem

`_forward_with_retry` ([proxy.py:62-74](../../app/src/pokeproxy/proxy.py) before this fix) had four compounding defects:

1. `while True` with no attempt cap and no overall deadline.
2. A new `httpx.AsyncClient()` constructed on **every attempt**, never closed.
3. That client used `timeout=600.0`, overriding the properly-configured `httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)` client built in `main.py`'s lifespan.
4. That lifespan-built client (`app.state.http_client`) was dead code — created, closed on shutdown, never read anywhere in `proxy.py`.

## Production Impact

A single slow downstream could pin a request for up to 600 seconds, not the apparent 10s read timeout. During a real outage, every in-flight request retried forever with jittered exponential backoff (capped at 30s between attempts) and never freed its connection slot.

Every attempt also leaked a connection pool. A downstream outage lasting minutes, at any sustained request rate, leaked a proportional number of unclosed clients and sockets — slow file-descriptor exhaustion, not just wasted memory.

Every other request path in the app fails in bounded time with a clear `outcome` (C5). This was the one exception: a downstream outage looked like a hang, not a `downstream_error`/`downstream_timeout`, until the process ran out of file descriptors.

## Evidence

Isolated `_forward_with_retry` against a refused connection for 3 seconds, before any change:

```
clients created in 3s    : 4
clients explicitly closed: 0
per-attempt timeout kwarg: 600.0
still retrying after 3s  : True
```

`grep` for `http_client` confirmed point 4 — built and closed in `main.py`, never referenced in `proxy.py`.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Retry policy | uncapped · fixed attempt cap · fixed cap + overall deadline | **cap + deadline** |
| Client lifecycle | new client per attempt (leaked) · new client per attempt, closed · reuse `app.state.http_client` | **reuse** |
| Per-attempt timeout | keep `600.0` · client's configured default | **client's configured default** |
| Customization | hardcoded constants · `Settings` fields | **`Settings` fields**, per explicit request |

## Decision

**Reuse `app.state.http_client`.** It already existed, was already correctly configured, and was already closed on shutdown — it just needed to be read. This deletes the leak outright.

**Cap attempts and add an overall deadline, both configurable via `.env`** — `FORWARD_MAX_ATTEMPTS` (default 3) and `FORWARD_DEADLINE_SECONDS` (default 10.0), validated at startup the same way as C1: non-positive values are refused before the process serves traffic, not discovered mid-outage.

**Correction, found later:** at the time this issue was closed, `.env.example` and `README.md` documented these as `POKEPROXY_FORWARD_MAX_ATTEMPTS`/`POKEPROXY_FORWARD_DEADLINE_SECONDS`. That was wrong — `Settings` has no `POKEPROXY_` prefix configured, so the field mapping was always the unprefixed name (matching `REDIS_URL`, `LOG_LEVEL`). The code itself worked correctly; only the docs and the validator error-message text named the wrong variable, so an operator following the README exactly as written would set a variable the process silently ignored. Fixed when the Redis timeout settings were added — see `docs/issues/005-unguarded-redis-calls.md`.

**Exhaustion re-raises the original exception type**, so the existing `httpx.TimeoutException` / `httpx.HTTPError` handlers in `_forward_request` produce `downstream_timeout` / `downstream_error` without any new outcome value. `httpx.ConnectError` is not a `TimeoutException` subclass (verified via MRO inspection), so a refused connection correctly lands as `downstream_error` and an actual timeout as `downstream_timeout`.

**Drop the `timeout=600.0` override** — the shared client's configured timeout applies once there's no more per-attempt client construction.

**Clock and sleep are injected, not imported directly.** `_forward_with_retry(..., clock=time.monotonic, sleep=asyncio.sleep)` — real functions by default, replaceable in tests. This surfaced during implementation: an earlier version of the deadline test monkeypatched the global `time.monotonic`, which is also read internally by asyncio's own event loop scheduler, and starved it after a handful of calls. Injecting the dependency instead of patching global state removed the flakiness and made the retry loop's timing logic testable in isolation without touching anything outside the function.

## Implementation

`RetryPolicy` — a frozen dataclass of `max_attempts` and `deadline_seconds` — replaces the ad-hoc loop state. `_next_backoff_delay` is now a pure function, independently testable. The retry loop re-raises with a bare `raise` inside the `except` block on exhaustion, so there's no sentinel variable to track a "last error" across iterations — every loop iteration either returns or raises, and the only exit path when `max_attempts >= 1` (enforced by the `Settings` validator) runs at least once.

`app.state.retry_policy` is built once at startup from `Settings`, alongside the already-existing `app.state.http_client`. `_forward_request` reads both from `request.app.state` and passes them through — the same pattern already used for `hmac_key`, `stats`, and `config_path`.

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13).

| Check | Result |
|---|---|
| Full suite | **38 passed** (was 28); 10 new (7 in `test_proxy.py`, 3 in `test_logging.py`) |
| `test_proxy.py` | isolated retry-loop tests: success without retry, recovery within the cap, exhaustion at the cap, exhaustion at the deadline (fake clock, no real sleeping), the shared client is never reconstructed, a successful first attempt never sleeps |
| `test_logging.py` — 3 tests unblocked by this fix | `forwarded`, `downstream_timeout`, `downstream_error` outcomes, previously undeliverable because C2's unbounded retry would have hung the test |
| `ruff check .` | clean |
| Manual, real request against a closed port, mocked downstream via `MockTransport` (Redis isn't running in this environment — see Tradeoffs) | `HTTP 502`, `outcome: "downstream_error"`, **`duration_ms: 276.69`** — 3 attempts, jittered backoff, clean failure. Previously this class of request had no bound |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| **Manual end-to-end verification required bypassing the cache lookup**, because Redis isn't running in this dev environment and C4 (unguarded Redis calls) is still open | Not a gap in this fix — it's C4, next in Wave 3. The bypass used the exact same monkeypatch pattern the automated tests already use, so the manual check exercises the identical code path a real request would take once C4 lands |
| Bounded retry means a downstream recovering just past the deadline now gets `downstream_error` where it previously (eventually) succeeded | Correct tradeoff — an unbounded wait is not a feature |
| Shared client means downstream connection-pool exhaustion is now shared across all forward targets | Acceptable: the pool is already sized for concurrent use, and per-attempt clients gave zero isolation benefit since they were never reused |
| `RetryPolicy` itself doesn't validate `max_attempts >= 1` | Deliberately left to `Settings`' validator — the two-tier trailing `raise AssertionError("unreachable")` in `_forward_with_retry` is the safety net if `RetryPolicy` is ever constructed directly with an invalid value outside `Settings` |
| Existing pre-C2 comments in `config.py`/`proxy.py` adjacent to the edited hunks were removed | Incidental to editing those exact lines under the new no-comments-in-app-code convention, not a broader unrelated refactor |
