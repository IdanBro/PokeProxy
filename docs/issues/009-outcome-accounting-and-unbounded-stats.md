# H4 + H5 — `/stats` lies during a total outage, and its memory grows forever

**Severity:** High (H4, H5) · **Wave:** 4 · **Status:** Fixed
**Files:** `app/src/pokeproxy/stats.py`, `app/src/pokeproxy/proxy.py`, `app/src/pokeproxy/main.py`

## Problem

**H4 — accounting drift.** `request_count` and `error_count` were incremented independently, in different branches: `request_count` only after a downstream response came back successfully ([proxy.py:132](app/src/pokeproxy/proxy.py:132), pre-fix), `error_count` in the timeout/error handlers instead. During a total downstream outage, every request hit the error handlers — `error_count` climbed, `request_count` never moved, and `error_rate = error_count / request_count` returned the zero-division guard's `0.0`, reporting a perfectly healthy service during a 100% failure rate. Separately, `bytes_received` was *assigned* (`= len(body)`) rather than accumulated, so it only ever reflected the most recent request. Rejections (bad signature, bad protobuf, oversized body) and `no_rule_matched` touched `stats` at all — they have no downstream URL to key on, so they were invisible in `/stats` entirely, not just undercounted.

This is literally structural observation #2 from the original Part 1 review ("`/stats` is wrong in precisely the way that hides an outage") — the logging half was fixed by C5, the accounting half was not.

**H5 — unbounded growth.** `_response_times` ([stats.py:15](app/src/pokeproxy/stats.py:15), pre-fix) was a plain list every request appended to via `bisect.insort` to keep it sorted, with nothing ever removing entries. Memory grew without bound, and each insert cost O(n), so both memory and per-request latency degraded with uptime. Its only consumer was `percentile()` — `avg_response_time` needs just `total_response_time`/`request_count`, both O(1).

## Production Impact

H4: a dashboard reading this endpoint would show `0.0` error rate during the exact event it exists to catch — a total downstream outage. H5: a slow memory/CPU creep visible only after sustained uptime, invisible in any short-lived test or demo.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| H4 fix shape | route all accounting through `main.py`'s existing logging middleware seam (would need bytes/URL plumbed onto `request.state`) · keep per-URL recording where it already happens, but make request/error counting atomic | **atomic per-branch recording** — `EndpointStats.record_request(is_error=bool)` updates both counters in one call, so they can never drift. Moving everything into the middleware would have required threading bytes_sent/response-time/URL through `request.state` and risked silently changing what "response time" measures (currently the downstream forward leg only) |
| Where rejections/no-rule-matched get counted | skip them (status quo) · give them their own URL-shaped bucket · **count by outcome name, separate from per-URL stats** | **outcome-keyed**, agreed with the user — they have no URL, and forcing one would be a fiction |
| H5 fix | streaming quantile structure (t-digest/HDR histogram) · fixed-size sample buffer (`deque(maxlen=N)`) · **delete per-sample storage and `percentile()` entirely** | **delete** — user call: percentiles are Prometheus/Grafana's job in Part 4 (`histogram_quantile()` over real histogram buckets, not a hand-rolled sample), and a bounded buffer would still be complexity in the app for a capability the app doesn't need to own. `avg_response_time` stays — it was already O(1) memory (`total_response_time`/`request_count`), never part of the H5 bug |

## Decision

**`EndpointStats.record_request(is_error: bool)`** replaces the two independent `+= 1` sites — used at all three exit points of `_forward_request` (success, timeout, downstream error), guaranteeing `error_count <= request_count` always holds. **`bytes_received`** changed from `=` to `+=`.

**New `StatsRegistry.record_outcome(outcome: str)`** — a flat `{outcome: count}` map, separate from the per-URL `endpoints` map. **New `_outcome_response()` helper** in `proxy.py` collapses every rejection branch and `no_rule_matched` into one call that sets `request.state.outcome` (unchanged, C5's seam) *and* records it into stats — same fix shape as C5 used for logging, applied to accounting. `main.py`'s `internal_error` handler gets the same one-line treatment, closing the same class of gap consistently rather than leaving one hole.

**`_response_times` and `percentile()` are deleted**, not bounded. Nothing else in the codebase read `percentile()` — its only purpose was to serve numbers `/stats` doesn't even expose in `to_dict()` today. `record_response_time()` now does exactly one thing: accumulate `total_response_time`, which backs `avg_response_time` (`total_response_time / request_count`) and was never the source of H5's bug — it's a running float, O(1) memory regardless of request volume.

`StatsRegistry.to_dict()` output shape changes from a flat `{url: {...}}` to `{"endpoints": {...}, "outcomes": {...}}`, since a bare `"outcomes"` key at the old flat level would have collided with a URL literally named that. No test or documented consumer relied on the old shape (confirmed by grep before making the change).

## Implementation

Three files, minimal diff: `stats.py` (data structures), `proxy.py` (recording call sites + the new helper), `main.py` (one line closing the `internal_error` gap). `README.md`'s `/stats` description updated to mention the outcome counts.

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13).

| Check | Result |
|---|---|
| New tests (`test_stats.py`) | **13 passed** — unit: error_rate reflects a total outage (was the exact 0.0-during-outage bug, now asserts 1.0); error_rate zero only when nothing failed; mixed outcomes produce a real rate; total_response_time accumulates across calls; avg_response_time divides correctly; avg_response_time of empty stats is 0.0; registry records outcomes without a URL; `to_dict` shape. End-to-end via `TestClient`: a rejected request (missing signature) is counted by outcome; a no-rule-matched request is counted by outcome; an unhandled exception is counted as `internal_error`; **3 simulated downstream failures in a row produce `request_count == 3`, `error_count == 3`, `error_rate == 1.0`** — the direct regression proof that the audit-flagged bug is closed; `bytes_received` accumulates across two requests to the same endpoint instead of being overwritten |
| Full suite | **86 passed** (was 73) |
| `ruff check .` | clean |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| `/stats` response shape changed (`{url: {...}}` → `{"endpoints": {...}, "outcomes": {...}}`) | Breaking for any external consumer of the old shape — none exist today (confirmed by grep); `/stats` is explicitly replaced by Prometheus in Part 4, so this shape has a known short remaining lifespan |
| `/stats` no longer reports response-time percentiles at all (average only) | Deliberate — user call, made explicitly to avoid building throwaway complexity that Part 4's Prometheus histograms do properly. Revisit only if Part 4 slips and `/stats` needs to live longer than planned |
| `record_response_time`'s "response time" still measures only the downstream forward leg, not the full request (HMAC verify + decode + cache lookup) | Unchanged from before this fix — flagged as out of scope to avoid conflating H4/H5 with a semantic change to what latency the metric represents |
