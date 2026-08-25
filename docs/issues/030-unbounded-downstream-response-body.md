# No size limit on the downstream response body

**Severity:** Major · **Wave:** final review · **Status:** Fixed
**Files:** `app/src/pokeproxy/proxy.py`

## Problem

The inbound request body is capped at `MAX_BODY_SIZE` (1 MiB), enforced while streaming (`_read_body_within_limit`, checked chunk-by-chunk so an unbounded payload is never fully buffered). There was no symmetric cap on what downstream returns: `_forward_with_retry` called `client.post(...)`, which eagerly buffers the entire response body into `resp.content` before returning, and that buffer then got base64-encoded into a JSON blob written to Redis.

## Production Impact

`rules.json`'s downstream URLs are operator-configured, not hardcoded — a misbehaving or compromised downstream can return an arbitrarily large body. Two symmetric risks the inbound cap already guards against, but the outbound path didn't: memory exhaustion in the pod from buffering an unbounded `resp.content`, and Redis bloat from base64-encoding that same unbounded body into every duplicate-payload cache entry.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Enforcement point | check `len(resp.content)` after `client.post()` returns (already-buffered) · **stream the response and cap while reading**, mirroring `_read_body_within_limit` | **stream and cap** — checking after `.post()` returns doesn't help; httpx has already allocated the full buffer by then. Streaming via `client.send(request, stream=True)` + `resp.aiter_bytes()` means the body is measured chunk-by-chunk and abandoned (`resp.aclose()`) the moment it crosses the limit, exactly like the inbound path |
| Return type of `_forward_with_retry` | keep `httpx.Response`, populate `._content` manually · **new `DownstreamResponse` dataclass** (`status_code`, `headers`, `content`) | **new dataclass** — manually setting a private httpx attribute after a manual stream read is exactly the kind of fragile workaround the house style avoids; a plain dataclass with the three fields the call site actually uses is boring and typed correctly. `_forward_request` and existing tests only ever touched `.status_code`/`.headers`/`.content`, so the call site needed no other changes |
| Retry semantics on oversized body | retry (transient-error style) · **fail immediately, don't retry** | **don't retry** — a `DownstreamResponseTooLarge` isn't a `RETRYABLE_ERRORS` member, so it naturally propagates out of the retry loop on the first attempt. Retrying against a downstream that just sent an oversized body isn't going to produce a smaller one |
| Limit value | separate constant · **reuse `MAX_BODY_SIZE`** | **reuse** — same 1 MiB cap already applied to the inbound side; no reason for the two directions to differ, and it avoids a second config knob |

## Decision

`_forward_with_retry` now builds the request explicitly (`client.build_request`) and sends it with `stream=True`, then reads the body through a new `_read_response_within_limit` helper — the mirror image of `_read_body_within_limit`: accumulate chunks, return `None` the moment cumulative size exceeds `MAX_BODY_SIZE`. On `None`, it raises `DownstreamResponseTooLarge` after closing the response (`resp.aclose()` in a `finally`, so the connection is released whether the read succeeded, failed, or hit the cap). On success it returns a `DownstreamResponse(status_code, headers, content)`.

`_forward_request` catches `DownstreamResponseTooLarge` as its own branch (before the generic `httpx.HTTPError` branch, though the two don't overlap since it isn't an `httpx` exception): counts `downstream_requests_total{result="error"}`, sets `outcome="downstream_response_too_large"`, returns a `502` with a clear JSON error body, and — like every non-2xx/failure path — does not call `cache_response`.

## Implementation

`proxy.py`: new `DownstreamResponse` dataclass and `DownstreamResponseTooLarge` exception near `RetryPolicy`; new `_read_response_within_limit`; `_forward_with_retry` rewritten to stream + cap instead of `client.post(...)`; `_forward_request` gains the `DownstreamResponseTooLarge` except branch.

## Verification

Run via Docker (`ghcr.io/astral-sh/uv:python3.13-bookworm-slim`, whole repo mounted): `ruff check .` clean, full suite passing (122 tests, was 111).

New tests:
- `test_proxy.py::test_downstream_response_over_the_size_cap_is_not_retried` — a response one byte over `MAX_BODY_SIZE` raises `DownstreamResponseTooLarge` and only one attempt is made (no retry).
- `test_proxy.py::test_downstream_response_at_exactly_the_size_cap_is_accepted` — the boundary is inclusive, matching `_read_body_within_limit`'s inbound semantics.
- `test_dedup.py::test_oversized_downstream_response_is_not_cached_and_the_next_duplicate_retries` — an oversized response then a normal one on retry proves the oversized body wasn't cached.
- `test_metrics.py::test_oversized_downstream_response_is_counted_as_a_downstream_error` — asserts `outcome="downstream_response_too_large"`/`status="502"` and `result="error"`.

## Tradeoffs / Remaining Risk

The cap only applies to the response *body*; a downstream sending an extremely large set of *headers* isn't bounded by this change (httpx has its own internal header-size limits, not something this fix touches — out of scope, no evidence it's exploitable here since `rules.json` downstreams are operator-configured, not arbitrary user input). Streaming the read also means a slow-but-eventually-compliant downstream now has its body-read time counted against `forward_attempt_timeout_seconds` exactly as before (no behavioral change there — `client.send(..., stream=True)` still honors the client's configured read timeout per chunk, same as the previous eager `client.post()`).
