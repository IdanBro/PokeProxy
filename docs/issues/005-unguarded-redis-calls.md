# C4 — Redis calls unguarded: a cache blip became a 500 on every request

**Severity:** Critical · **Wave:** 3 · **Status:** Fixed
**Files:** `app/src/pokeproxy/cache.py`, `main.py`

## Problem

`get_cached_pokemon` and `cache_pokemon` called Redis directly with no error handling and no socket/connect timeout. Two distinct failure modes, both unhandled:

1. **Redis unreachable** — any `redis.exceptions.RedisError` propagated straight out of `stream()`, killing the request with a 500.
2. **Redis reachable but hung** — no timeout was configured, so a slow or wedged Redis blocked the request indefinitely rather than failing.

The cache exists to "avoid re-processing previously seen payloads" — the README's own words. A miss just means decoding the payload normally. Redis is not something the service needs to function, but the code treated it as if it were.

## Production Impact

Every request that reached the cache lookup depended on Redis being both reachable and fast, even though nothing about the request actually required it. A Redis restart, a network blip, or a slow Redis under its own load would fail 100% of traffic through `/stream`, not just degrade it — the opposite of what a best-effort cache is supposed to do.

This was not hypothetical: it was observed directly while verifying C1, C2, and C3 in this same dev environment, where Redis isn't running. Every one of those verification runs hit this exact bug before it was fixed.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Where to guard | at the `proxy.py` call sites · inside `cache.py` itself | **inside `cache.py`** |
| What to catch | bare `except Exception` · `redis.exceptions.RedisError` | **`RedisError`** |
| Client-level timeout | none (current) · `socket_connect_timeout` + `socket_timeout` | **both, 2.0s each** |
| Redis timeout customization | hardcoded constants · `.env`-configurable `Settings` fields | **hardcoded constants** — not requested this round, avoids expanding config surface beyond what's needed |

## Decision

**Guard inside `cache.py`.** Best-effort semantics are a property of the cache abstraction itself, not something every caller should have to remember to implement. `proxy.py` calls `get_cached_pokemon`/`cache_pokemon` exactly as before — no changes needed there, because the functions now can't raise `RedisError` outward at all.

**Catch `redis.exceptions.RedisError`** — the common base for every failure mode redis-py raises (`ConnectionError`, `TimeoutError`, `ResponseError`; verified via MRO inspection). Not a bare `except Exception`, which would also swallow real bugs like a `TypeError` from malformed data.

**A lookup failure returns `None`** (a cache miss); **a write failure returns normally** (the payload just isn't cached this time). Both log a `WARNING` with the exception attached, so a Redis outage is visible in the logs without breaking the request it's degrading.

**2.0-second socket and connect timeouts on the client.** Fast enough that a hung Redis can't meaningfully stall a request, generous enough not to misfire under normal load. Not made configurable via `.env` this round since it wasn't requested — flagged as a natural candidate for the same treatment C2's retry settings got, if it comes up.

## Implementation

`cache.py` wraps each Redis call in its own `try`/`except RedisError`, logging a distinct message per failure mode (`"cache lookup failed, treating as a miss"` / `"cache write failed, continuing without caching"`) with `exc_info=True`, so the traceback renders in full underneath the JSON record — consistent with how C5 already handles every other exception in this codebase.

`main.py`'s `aioredis.from_url(...)` call gains `socket_connect_timeout` and `socket_timeout`, both set from two module-level constants next to the other client construction.

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13). No live Redis in this environment (same limitation as C3), so unit tests use a fake that raises `RedisError` on every call, and one live check simulates a genuinely hung Redis with a raw TCP server that accepts a connection and never responds.

| Check | Result |
|---|---|
| New tests (`test_cache.py`) | **4 passed** — lookup failure returns `None` and logs, write failure doesn't raise and logs |
| Same 4 tests run against **pre-C4** `cache.py` | **4 failed** — every one raised `redis.exceptions.ConnectionError` uncaught, confirming they catch the real bug |
| Full suite | **48 passed** (was 44) |
| `ruff check .` | clean |
| Real end-to-end, unreachable Redis (connection refused) | full request through `TestClient`, no mocking: `WARNING cache lookup failed` → `WARNING cache write failed` → request proceeds → 502 `downstream_error` (nothing listening on the forward target either) in **727.8ms**. No 500, no crash |
| Real end-to-end, **hung** Redis (accepts, never responds) | raw asyncio TCP server standing in for Redis; `get_cached_pokemon` raised `redis.exceptions.TimeoutError` after the 2.0s socket timeout, caught, logged, returned cleanly — process exit 0 in **2.88s** total, not a hang |

The hung-Redis check is the more important of the two: it's the half of C4 that a simple "is Redis running" test can't exercise, since a refused connection fails fast on its own regardless of timeout configuration.

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| Redis timeouts were hardcoded at the time this issue was first closed | **Superseded** — made `.env`-configurable in a follow-up (`REDIS_CONNECT_TIMEOUT_SECONDS`, `REDIS_SOCKET_TIMEOUT_SECONDS`, both default 2.0, both validated `> 0`). That follow-up also caught and fixed a real naming bug: C2's `FORWARD_MAX_ATTEMPTS`/`FORWARD_DEADLINE_SECONDS` were documented with a `POKEPROXY_` prefix that the code never actually read — silently ignored if set exactly as the README said. All four operational settings now share one `_check_positive_seconds` validator keyed off `ValidationInfo.field_name`, so the error message always names the real variable |
| Stale comments in `test_logging.py` referencing "C4 is still open" | Updated in this change (not a new bug, but they became actively wrong the moment this landed, so left as-is would have been worse than the original TODO-style note) |
| `no_cache` fixture in `test_logging.py` is no longer load-bearing for correctness | Kept anyway — real network calls in unit tests are still the wrong default even when they now degrade gracefully, and removing it would make several tests ~hundreds of ms slower for no benefit |
| A Redis outage is now silent to the client but loud in logs only | Matches the assignment's own framing of the cache as best-effort. `/stats`/metrics still don't surface cache health as a first-class signal — that's Part 4 scope (Prometheus), not this fix |
