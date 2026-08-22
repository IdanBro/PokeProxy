# R1 — Per-attempt HTTP timeout equalled the whole retry deadline, so a slow downstream never retried

**Severity:** Should fix (found in the final Part 1 audit, 2026-08-22) · **Status:** Fixed
**Files:** `app/src/pokeproxy/config.py`, `main.py`

## Problem

C2 (`docs/issues/003-unbounded-forward-retry.md`) bounded the retry loop with `FORWARD_MAX_ATTEMPTS` and `FORWARD_DEADLINE_SECONDS`, but the shared `httpx.AsyncClient`'s per-attempt `read`/`write` timeout stayed hardcoded at `main.py:70` — `10.0`, the same value as the default deadline. The retry loop (`proxy.py`'s `_forward_with_retry`) checks the deadline *between* attempts, not during one, so a single attempt that hangs for the full timeout consumes the entire budget before a second attempt ever gets scheduled.

## Production Impact

`FORWARD_MAX_ATTEMPTS` is documented in `app/README.md` as "max attempts forwarding to a downstream before giving up." Against the actual production failure mode it names — a slow or hung downstream, not a refused connection — it did nothing. Measured against a socket that accepts a connection and never responds:

```
attempts made : 1 (max_attempts=3)
elapsed       : 10.17s (deadline=10.0s)
raised        : ReadTimeout
```

Against a refused connection (fast to fail) it worked as documented — 3 attempts in 0.48s. So the bug was invisible to the connection-refused testing this project already had (C2's own verification, and `test_proxy.py`'s retry-loop tests, all use instant-failing transports) and only shows up against the failure mode retries exist for. The client still failed in bounded time either way — this is a wrong-knob bug, not an availability regression — but a transient blip that a second attempt would have absorbed instead surfaced as a hard failure to the caller.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Where the per-attempt bound comes from | derive it automatically from `deadline / max_attempts` · a separate configured value | **separate configured value** — an automatic derivation changes silently whenever `max_attempts` or `deadline` change, which is exactly the kind of implicit coupling this project has been removing (C2's own retry policy is explicit for the same reason) |
| Default | leave read/write unbounded and rely on the deadline alone · a bounded default well under the existing deadline default | **3.0s**, under the existing 10.0s default — gives up to 3 real attempts inside the default deadline instead of consuming it in one |
| Guarding misconfiguration | trust the operator to keep attempt_timeout < deadline · fail startup if they don't | **fail startup** — this is the exact bug being fixed, so leaving it possible to reintroduce via a bad `.env` value would be fixing the symptom, not the cause |

## Decision

New `Settings.forward_attempt_timeout_seconds` (`FORWARD_ATTEMPT_TIMEOUT_SECONDS`, default `3.0`), validated positive through the existing `_check_positive_seconds` validator plus a new `model_validator(mode="after")` that rejects `forward_attempt_timeout_seconds >= forward_deadline_seconds` by name, naming both variables in the error. `main.py` gains `_build_http_client(settings)`, mirroring the `_load_settings`/`_load_rules` naming convention, so the client construction is a named, independently testable unit instead of an inline expression in `lifespan()`. `connect`/`pool` stay at their existing `5.0` — the measured failure mode was a downstream that accepts a connection and then never responds, not a slow connect, so only `read`/`write` needed to move.

## Implementation

```python
# config.py
forward_attempt_timeout_seconds: float = 3.0
...
@model_validator(mode="after")
def _check_attempt_timeout_fits_deadline(self) -> Settings:
    if self.forward_attempt_timeout_seconds >= self.forward_deadline_seconds:
        raise ValueError(...)  # names both variables
    return self
```

```python
# main.py
def _build_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=5.0,
            read=settings.forward_attempt_timeout_seconds,
            write=settings.forward_attempt_timeout_seconds,
            pool=5.0,
        )
    )
```

`lifespan()` now calls `_build_http_client(settings)` instead of constructing the client inline.

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13.15).

| Check | Result |
|---|---|
| Full suite | **101 passed** (was 94); 7 new — 5 in `test_config.py`, 2 in new `test_retry_timeout.py` |
| `test_config.py` | `FORWARD_ATTEMPT_TIMEOUT_SECONDS` takes effect via env; rejects `<= 0`; rejects `>=` the deadline (both the equal case — the exact default-config bug — and the longer-than case), naming both variables; accepts a value strictly less than the deadline |
| `test_retry_timeout.py` — real TCP, not a mock transport | A custom `httpx.AsyncBaseTransport` that returns instantly bypasses httpx's timeout machinery entirely, so this needed a real socket. `_HungServer` accepts connections on an ephemeral port and never responds. Against `attempt_timeout=0.3s`/`deadline=1.0s`: **≥2 connections accepted**, loop finishes within the deadline. Also asserts `_build_http_client` actually wires `read`/`write` to the configured attempt timeout, not the deadline |
| `ruff check .` | clean |
| Live re-run of the original audit probe, now against the real app's `_build_http_client` + default `Settings` (attempt_timeout=3.0, deadline=10.0), same black-hole socket as the audit | **3 of 3 attempts in 9.70s** — was 1 of 3 in 10.17s before this fix |

Writing the regression test surfaced an unrelated asyncio behavior worth recording: `asyncio.Server.wait_closed()` in Python 3.13 also waits for every already-accepted connection's handler to finish, not just for the listening socket to stop. A handler that never returns (as ours deliberately doesn't, to simulate a hang) makes `wait_closed()` hang too. `_HungServer.__aexit__` cancels the handler tasks explicitly before calling `wait_closed()`.

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| `connect`/`pool` timeouts unchanged at `5.0` | Deliberate — the measured failure mode was a hung response, not a slow connect; changing an axis with no evidence of a problem would be scope creep on a "should fix" item |
| Default `3.0s` × up to 3 attempts can still exceed the default `10.0s` deadline before backoff is accounted for | Not a regression — the deadline check between attempts (already in place since C2) still cuts the loop off at 10.0s regardless; this fix restores the ability to *attempt* a retry, it doesn't change the existing deadline-enforcement behavior |
| Cross-field validation only checks `attempt_timeout < deadline`, not that they leave room for backoff between attempts | Acceptable — the loop's own deadline check (C2) already handles that at runtime; adding a second, backoff-aware startup check for a cosmetic edge case (fewer attempts than `max_attempts` when backoff eats the remainder) would be validating a scenario that degrades safely on its own |
