# M4 — The cache did nothing useful; a hit still forwarded downstream anyway

**Severity:** Medium · **Wave:** 3 · **Status:** Fixed
**Files:** `app/src/pokeproxy/cache.py`, `app/src/pokeproxy/proxy.py`, `app/src/pokeproxy/config.py`, `app/src/pokeproxy/main.py`

## Problem

The cache stored the decoded protobuf payload, keyed on a SHA-256 of the raw body. A hit skipped re-decoding — a microsecond-scale operation — at the cost of a Redis round trip (hundreds of microseconds), and even on a hit the request still got routed and forwarded to downstream exactly as if it were new. The cache spent more than it saved and prevented zero duplicate deliveries, which is what "avoid re-processing previously seen payloads" (the README's own framing) actually implies.

## Production Impact

A client retry, a load balancer double-send, or any at-least-once delivery mechanism upstream of this proxy would produce duplicate downstream deliveries with no protection, despite the presence of a caching layer that looked like it should prevent exactly that.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| What a cache hit does | skip decode only (status quo) · skip forward, return a synthetic "duplicate" marker · **skip forward, replay the actual cached downstream response** | **replay the cached response** — transparent to the client, matches how idempotency-key caching works in real systems; a synthetic marker would leak an implementation detail the client has no use for |
| Which outcomes get cached | every attempt, including failures · **only a real downstream response (`forwarded`)** | **`forwarded` only** — a downstream 4xx/5xx business response is a legitimate answer worth replaying; `downstream_timeout`/`downstream_error` are the proxy's own failure to deliver, and caching those would keep replaying a stale failure to every duplicate even after downstream recovers |
| Rules-config staleness (H1 interaction) | mix the rules config hash into the cache key so a rules change invalidates dedup automatically · **accept and document the residual risk** | **accept** — Redis persists across pod restarts regardless of H1, so a cached response can outlive a rules change for up to the TTL either way; mixing in a config hash adds real complexity for a narrow, short-lived edge case |
| Dedup window (TTL) | keep hardcoded · **make `.env`-configurable** | **configurable** (`CACHE_TTL_SECONDS`, default 300.0) — same pattern as `FORWARD_MAX_ATTEMPTS`/`REDIS_*_TIMEOUT_SECONDS`; the TTL is now a business-visible dedup window, not an implementation detail |

## Decision

`cache.py` now stores the **downstream response** — status code, the same filtered headers already relayed to the client (`_forwardable_response_headers`, reused from H2), and the content, base64-encoded inside a JSON envelope so arbitrary (non-UTF-8) response bodies round-trip correctly. `get_cached_response`/`cache_response` replace `get_cached_pokemon`/`cache_pokemon`.

`proxy.py`'s `stream()` checks the cache immediately after signature verification. A hit short-circuits entirely — no decode, no rule matching, no forward — via `_duplicate_response()`, which sets `request.state.outcome = "duplicate_suppressed"`, records it through the same outcome-keyed stats seam H4 built, and replays the stored response. A miss proceeds exactly as before (decode → match → forward), and `_forward_request` now caches the response itself, inline in its success branch, only when a response actually came back from downstream — the timeout/error branches never call `cache_response`.

A `duplicate_suppressed` replay does **not** touch the per-URL `EndpointStats` (`request_count`/`bytes_sent`) — no network call to downstream happened, so counting it there would misrepresent real downstream traffic volume. It's tracked purely via `StatsRegistry.record_outcome()`.

`Settings.cache_ttl_seconds` joins the four existing operational settings sharing `_check_positive_seconds`.

## Implementation

`cache.py` rewritten around the new response shape (no more `PokemonJSON` import — it never needs one). `proxy.py`: `_forward_request` gains a `cache_key: str` parameter and caches on success; new `_duplicate_response()` helper mirrors `_outcome_response()`'s shape for a non-JSON `Response`. `config.py`/`main.py`: one field, one validator entry, one `app.state` assignment. `.env.example`/`README.md` updated (`CACHE_TTL_SECONDS`, the `How It Works` diagram, the outcome list, the `/stats` description).

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13). No live Redis in this environment, so end-to-end dedup tests inject a minimal in-memory `FakeRedis` as `app.state.redis` (same pattern C3/C4 already established for cache unit tests, extended here to exercise the full `/stream` path).

| Check | Result |
|---|---|
| `test_cache.py` (rewritten for the new API) | **11 passed** — miss returns `None`; a hit returns the exact stored status/headers/content; arbitrary binary content round-trips through the base64 envelope; no keyspace scan (C3 regression preserved); lookup cost independent of keyspace size; write uses the caller-supplied TTL; failure modes degrade to a miss/no-op and log a `WARNING` (C4 regression preserved) |
| `test_config.py` (+2 cases) | `CACHE_TTL_SECONDS` configurable via env; rejected when non-positive — same parametrized proof used for the other four operational settings |
| New `test_dedup.py` | **5 passed**, end-to-end through `TestClient` with a real (fake) Redis and a mocked downstream: a duplicate payload replays the cached response and the mocked downstream is called exactly once, not twice; the replay is counted as `duplicate_suppressed`; the replay does **not** inflate `EndpointStats.request_count`; a downstream failure is never cached — a duplicate sent after downstream recovers gets a fresh, successful attempt (call count 2, not 1); two genuinely different payloads are never deduplicated against each other |
| Full suite | **94 passed** (was 86) |
| `ruff check .` | clean |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| A rules change can leave a cached response replaying under the old routing for up to `CACHE_TTL_SECONDS`, even after the pod restart H1 requires | Accepted, documented (see Decision) — Redis persists across restarts regardless of this cache's design, so this isn't new risk introduced by M4, just restated with the response now being what's stale instead of just a routing decision |
| Downstream must be idempotent | Already true under C4 (Redis outages produce duplicate deliveries, not dropped ones) — unchanged and restated, not a new requirement from M4 |
| A payload that matches no rule is never cached | Deliberate scope boundary — there's no downstream response to remember, so a repeat of a non-matching payload re-runs decode + rule-matching every time. Consistent with "the cache remembers forwarding attempts," not "the cache remembers everything" |
| `scripts/load_generator.py` mostly stops exercising the forward path (12 fixed payloads, byte-identical serialization → 12 distinct hashes, everything else suppressed within the TTL) | Already flagged in `docs/planning/part-01-production-hardening.md`'s M4 note before this fix landed — unaffected by this change; closed later in Part 4 step 2 (`docs/planning/part-04-observability.md`), which randomizes `number` per payload |
| Part 3 post-deploy E2E must use a unique payload per run or flush the dedup key first | Already flagged before this fix landed — closed in Part 3: `e2e_check.py` randomises `number` and `name` each run for exactly this reason (`docs/planning/part-03-cicd-gitops.md`, Step 3) |
