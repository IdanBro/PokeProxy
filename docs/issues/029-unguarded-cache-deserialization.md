# A malformed cache entry crashed every duplicate request instead of missing

**Severity:** Major · **Wave:** final review · **Status:** Fixed
**Files:** `app/src/pokeproxy/cache.py`

## Problem

`get_cached_response` (`cache.py:31-35` pre-fix) wrapped only `redis.get()` in `try/except RedisError`. `json.loads(data)`, the `stored["status_code"]`/`stored["headers"]`/`stored["content_b64"]` key accesses, and `base64.b64decode(...)` all ran unguarded after that. Also, `cache_operations_total{result="hit"}` was incremented **before** any of that deserialization ran, so a corrupt entry still counted as a "hit" right up until it crashed the request.

## Production Impact

Any cache entry whose shape doesn't match what the current code expects — a schema change deployed mid-rollout against a Redis instance shared by old and new pods, a partial write, bit rot, a manual `redis-cli` poke during an incident — raises `JSONDecodeError`/`KeyError`/`binascii.Error` uncaught. That's an unhandled exception inside `/stream`'s request path, turning into a `500` for every duplicate of that payload until the entry's TTL expires (up to `CACHE_TTL_SECONDS`, default 300s) and Redis evicts it. A single bad write can degrade the exact traffic pattern (duplicates) the cache exists to protect.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Failure handling | let it crash (status quo) · catch `Exception` broadly · **catch the specific decode/shape errors** (`KeyError`, `TypeError`, `ValueError`) | **specific catch** — `json.JSONDecodeError` and `base64`'s `binascii.Error` both subclass `ValueError`, and a wrong-shaped entry (missing key, wrong type) raises `KeyError`/`TypeError`. A bare `except Exception` would also swallow real bugs in this function unrelated to cache content |
| Where the "hit" counter fires | before deserialization (status quo, bug) · **after deserialization succeeds** | **after** — a `hit` that isn't actually usable is indistinguishable from a real hit on the dashboard unless it's re-labeled `error` instead, matching how a Redis-unavailable lookup is already labeled |
| Behavior on failure | treat as a miss (re-forward, don't cache-block) · treat as an error response | **treat as a miss** — mirrors the existing `RedisError` handling immediately above it; the request proceeds through the normal decode → match → forward path exactly as if nothing were cached, which is always safe (worst case: one avoidable forward instead of a crash) |

## Decision

The deserialization block (`json.loads` + key access + `base64.b64decode`) is now inside its own `try/except (KeyError, TypeError, ValueError)`, separate from the `RedisError` guard around `redis.get()`. On failure: log a warning (`"cache entry malformed, treating as a miss"`, `exc_info=True`), increment `cache_operations_total{operation="get", result="error"}`, return `None`. The `hit` increment moved to after the `try` block succeeds, so it only fires once the entry is confirmed usable.

## Implementation

`cache.py:get_cached_response` — inner `try/except` added around the three deserialization steps; `result="hit"` increment relocated below it.

## Verification

Run via Docker (`ghcr.io/astral-sh/uv:python3.13-bookworm-slim`, whole repo mounted): `ruff check .` clean, full suite passing (122 tests, was 111).

New tests in `test_cache.py`:
- `test_malformed_json_entry_is_treated_as_a_miss` — non-JSON stored value.
- `test_entry_missing_a_required_key_is_treated_as_a_miss` — valid JSON, missing `content_b64`/`headers`.
- `test_entry_with_invalid_base64_content_is_treated_as_a_miss` — valid JSON shape, unparseable base64.
- `test_malformed_entry_is_logged` — warning text present.
- `test_malformed_entry_is_counted_as_an_error_not_a_hit` — asserts `result="error"` is incremented and `result="hit"` is not, closing the exact pre-fix ordering bug.

## Tradeoffs / Remaining Risk

None identified — this mirrors an already-established pattern (`RedisError` → miss) for a different failure class in the same function, with no behavioral change on the happy path.
