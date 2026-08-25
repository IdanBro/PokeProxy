# M1 + H7 — No readiness concept, nothing tells Kubernetes to stop routing before shutdown

**Severity:** Medium (M1) + High (H7) · **Wave:** 4 · **Status:** Fixed
**Files:** `app/src/pokeproxy/main.py`, `app/README.md`

## Problem

`/health` was the only probe endpoint and returned a hardcoded `{"status": "alive"}` regardless of app state. It answered "is the process running," not "is this pod ready to receive traffic" — two different questions Kubernetes needs answered differently (liveness vs. readiness). With one endpoint covering both, there was no way to signal "still starting up" or "shutting down, stop sending me traffic."

## Production Impact

Liveness and readiness serve different purposes: liveness triggers a restart, readiness controls Service endpoint membership. Collapsing them into one always-true endpoint meant:

- A pod mid-startup (rules not yet loaded, clients not yet created) looked identical to a fully-up pod.
- On shutdown, nothing in the app signaled "not ready" before the process exited, so a readiness probe polled during a rollout would keep reporting healthy right up to termination — the app-side half of why in-flight rollouts can drop traffic.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Endpoint naming | `/healthz` + `/readyz` (matches kubelet's own probe naming) · keep `/health`, add `/ready` | **keep `/health`, add `/ready`** — smaller diff, no rename risk against the existing documented endpoint; discussed and explicitly chosen over the `-z` convention |
| Should readiness check Redis | yes · no | **no** — a single Redis blip would un-ready every pod at once, defeating the point of C4's best-effort cache design |
| Readiness signal source | poll dependencies on each request · a flag flipped once at startup/shutdown | **flag on `app.state`**, set by the `lifespan` context manager |

## Decision

**`/health` unchanged (liveness, always cheap/trivial). New `/ready` reads `app.state.ready`**, a bool set `True` at the end of `lifespan`'s startup section (after rules, HMAC key, and both clients are set up) and `False` as the very first line after `yield` resumes — i.e., before Redis/HTTP client cleanup runs, so it flips as early as possible in the shutdown sequence. `/ready` returns 503 when the flag is false, 200 otherwise.

Readiness deliberately does **not** probe Redis, matching the Part 1 decision recorded in `docs/planning/part-01-production-hardening.md`: Redis is best-effort (C4), and correlated readiness failure across a whole deployment on a Redis blip would be worse than the degradation it's meant to prevent.

## Implementation

Three small changes, no new files in `src/`: `app.state.ready = True`/`False` at the two lifespan boundaries; a `GET /ready` route mirroring the existing `/health`/`/stats` handlers; `/ready` added to `_UNLOGGED_PATHS` alongside `/health`/`/stats` since kubelet-style probes poll continuously and shouldn't bury real traffic in the access log.

This is the **app-side half** of graceful shutdown only. The other half — a `preStop` hook and `terminationGracePeriodSeconds` on the Deployment, giving Kubernetes time to actually notice the flip and stop routing before `SIGTERM` — is a Part 2 manifest concern, closed there via `lifecycle.preStop.sleep` + `terminationGracePeriodSeconds` (`docs/planning/part-02-infrastructure-deployment.md`, H7).

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13).

| Check | Result |
|---|---|
| New tests (`test_readiness.py`) | **4 passed** — `/ready` returns 200 after startup; returns 503 when the flag is forced false; `app.state.ready` is false after the `TestClient` context exits (shutdown ran); `/health` is unaffected by readiness state |
| New test (`test_logging.py`) | **1 passed** — `/ready` produces no access line, same as `/health`/`/stats` |
| Full suite | **67 passed** (was 62) |
| `ruff check .` | clean |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| Readiness flip alone doesn't guarantee zero dropped requests | The K8s-side `preStop`/grace-period wiring that makes the flip actually effective is Part 2 scope, closed there and live-verified under a rolling restart at 30 rps (2487 requests, 0 errors) — `docs/planning/part-02-infrastructure-deployment.md` |
| No explicit `SIGTERM` handler in the app itself | Not needed — uvicorn's default signal handling already stops accepting new connections and drains in-flight ones before running the ASGI lifespan shutdown phase where this flag flips; adding a second handler would risk double-handling the signal for no benefit |
| Readiness never reflects Redis health | Deliberate — see Decision. `/stats`/metrics remain the place cache health would surface as a first-class signal, which is Part 4 scope |
