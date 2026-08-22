# C3 — cache lookup scanned the entire Redis keyspace instead of one GET

**Severity:** Critical · **Wave:** 3 · **Status:** Fixed
**Files:** `app/src/pokeproxy/cache.py`

## Problem

`get_cached_pokemon` ([cache.py:14-24](../../app/src/pokeproxy/cache.py) before this fix) fetched **every** `pokeproxy:pokemon:*` key from Redis with `KEYS`, then linearly scanned that list in Python for a byte-string match, then issued a second round trip with `GET` once it found the match. All to answer a question Redis already answers in one call: "does this exact key exist?"

## Production Impact

This ran on every request that reached the cache lookup. The candidate list `KEYS` returns grows with the number of distinct payloads seen inside the cache TTL, so the per-request cost scales with total cache occupancy, not with anything about the current request. Latency degrades as the service stays up and traffic increases — the opposite of what a cache is supposed to do — and it's invisible in any of today's metrics until someone is already under load asking why `/stream` got slower.

`KEYS` is also a known Redis anti-pattern independent of this app: it's O(N) over the whole keyspace and blocks the single-threaded Redis event loop while it runs, so it's a shared-tenancy risk if this Redis instance is used by anything else.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Lookup method | keep `KEYS` + scan · `GET` directly · `EXISTS` then `GET` | **`GET` directly** |

There wasn't a real second option here — the cache key is already known and deterministic (`make_cache_key(body_hash)`), so `GET` answers "does it exist and what is it" in exactly the call this needs.

## Decision

Replace the scan with a single `redis.get(cache_key)`. A miss returns `None` directly from Redis instead of being inferred from an empty scan result.

## Implementation

`get_cached_pokemon` is now three lines: one `GET`, a `None` check, one `json.loads`. No behavior change — same cache key format, same TTL, same JSON shape returned to the caller. `cache_pokemon` and `make_cache_key` were untouched functionally; their docstrings were dropped in the same pass since the file is small, entirely in scope for this issue, and the names already say what each function does.

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13). No live Redis is available in this environment (established in earlier sessions), so verification uses a minimal in-memory fake implementing the `get`/`set`/`keys` subset the code actually calls — this is the correct tool for testing the *access pattern*, which is the bug, independent of whether a real Redis is reachable.

| Check | Result |
|---|---|
| New tests (`test_cache.py`) | **6 passed** |
| Same 6 tests run against **HEAD's** `cache.py` | **1 failed** — `test_lookup_never_scans_the_keyspace` asserted `redis.keys_calls == 0` and got `1`, catching the exact bug |
| Cost independent of keyspace size | fake store seeded with 500 unrelated keys plus the target; `get_calls == 1` |
| Full suite | **44 passed** (was 38) |
| `ruff check .` | clean |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| No live-Redis end-to-end verification | Same limitation as prior Part 1 sessions — no Redis server in this dev environment. The fake-based tests directly exercise the code path that was wrong; a real Redis would only add confidence that `GET`/`SET` semantics match the fake, which they trivially do |
| Redis calls are still unguarded, no timeouts | **C4**, next in Wave 3 — a Redis blip still becomes a 500 on 100% of traffic regardless of this fix |
