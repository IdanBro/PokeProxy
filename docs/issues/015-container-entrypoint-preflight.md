# M6 + R4 — Dead `POKEPROXY_PORT` config, and a config failure buried in uvicorn's own traceback

**Severity:** Medium/Low cost (M6) · Nice to have (R4) · **Part:** 2, step 2 · **Status:** Fixed
**Files:** `app/src/pokeproxy/__main__.py` (new), `app/Dockerfile`

## Problem

Two independent gaps, both rooted in the same fact: the process was always started via the `uvicorn` CLI directly (`uvicorn pokeproxy.main:app --host 0.0.0.0 --port 8000`), never through any code of ours.

- **M6:** `Settings.pokeproxy_port` is a real, validated, documented field (`.env.example`, `app/README.md`). Nothing read it. The actual listening port came from the CLI's hardcoded `--port 8000` flag. An operator who set `POKEPROXY_PORT=9001` expecting it to work would get a service still listening on 8000, silently.
- **R4:** `Settings()` is constructed lazily, inside the FastAPI `lifespan` context, which only runs *after* uvicorn has already started its own asyncio server machinery. A bad configuration produced the intended single `CRITICAL` line from `_load_settings()` — and then uvicorn's own ~20-line `SystemExit: 1` lifespan traceback rode along behind it, because uvicorn itself doesn't know startup failed for a reason that had already been logged cleanly.

## Production Impact

M6 is a "useful error messages"-adjacent gap in the sense the assignment names directly: a documented, validated setting that quietly does nothing is worse than no setting at all, because it looks like it should work. R4 is pure noise, not a defect — but noise matters at 3 AM: the actionable line (`configuration invalid, refusing to start`, naming the exact field) is followed by twenty lines that say nothing new, which is exactly the kind of signal-to-noise problem that makes a real on-call read `kubectl logs` slower than it needs to be during a CrashLoopBackOff.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Where to validate config | leave it in the FastAPI lifespan (current) · construct `Settings` in a dedicated entrypoint before uvicorn starts | **dedicated entrypoint** |
| How to hand off to uvicorn | keep the CLI (`CMD ["uvicorn", ...]`) · call `uvicorn.run()` programmatically | **`uvicorn.run()`**, since only a real Python entrypoint can validate first and then decide whether to start uvicorn at all |
| Logging risk of switching to `uvicorn.run()` | accept whatever uvicorn's default logging setup does · explicitly disable it | **explicitly disable it** (`log_config=None`) — see Implementation |

## Decision

A new `app/src/pokeproxy/__main__.py`, invoked via `python -m pokeproxy` (`Dockerfile`'s `CMD`). It reuses `main.py`'s existing `_load_settings()` — no duplicated validation logic — and only calls `uvicorn.run()` if that succeeds. A failure now exits before uvicorn's own asyncio machinery ever starts, so there is nothing left for it to unwind into a traceback.

## Implementation

```python
def main() -> None:
    settings = _load_settings()
    uvicorn.run(
        "pokeproxy.main:app",
        host="0.0.0.0",
        port=settings.pokeproxy_port,
        log_config=None,
    )
```

`log_config=None` is load-bearing, not incidental. `pokeproxy.main` installs the project's JSON logging config as an *import-time* side effect (`setup_logging()`), which clears the default handlers on `uvicorn`/`uvicorn.error`/`uvicorn.access` and sets `propagate=True` so their records reach the JSON handler on root. Importing `_load_settings` from `__main__.py` triggers that import before `uvicorn.run()` is ever called. Left at its default, `uvicorn.run()` would call its own `logging.config.dictConfig()` afterward and silently reinstall handlers with `propagate=False` on those three loggers — undoing the JSON setup for every uvicorn-originated log line (`Started server process`, `Application startup complete`, etc.), which would have been a regression hiding behind an otherwise-correct fix.

## Verification

| Check | Result |
|---|---|
| Bad config (`POKEPROXY_HMAC_KEY` unset), real container | **1 line** of output — `CRITICAL configuration invalid, refusing to start` — exit code **1**, no uvicorn traceback |
| `POKEPROXY_PORT=9001`, real container | `/health` answers on port 9001; log line reads `Uvicorn running on http://0.0.0.0:9001` |
| JSON logging survives the switch to `uvicorn.run()` | Confirmed live — uvicorn's own startup lines still render as JSON, not plaintext |
| New tests (`tests/test_entrypoint.py`, `uvicorn.run` mocked) | 5 new: bad config exits before `uvicorn.run` is called and logs one `CRITICAL` line; custom port reaches the `port` kwarg; default is 8000; app import string is `"pokeproxy.main:app"`; `log_config=None` is passed |
| Full suite, three working directories | **106 passed** each time (101 → 106) — M7-CWD's CWD-independence survives the new entrypoint |
| `ruff check .` | clean |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| None identified | This is entrypoint-only — no request-path code (`main.py`, `proxy.py`, `config.py`) changed |
