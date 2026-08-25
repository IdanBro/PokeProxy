# Request body was fully buffered before the size check ran

> Written under the token-economy rule (`CLAUDE.md`): maximum information in minimum cost.
> Tables over prose. Keep evidence, exact numbers, `file:line` refs and honest uncertainty;
> cut restatement, hedging and filler. Terse is the goal; vague is a failure.

**Severity:** Medium (M2, app-level half) · **Wave:** final review · **Status:** Fixed
**Files:** `app/src/pokeproxy/proxy.py`

## Problem

`stream()` used to call `await request.body()`, which fully materializes the ASGI body into memory before any size check ran — the `len(body) > MAX_BODY_SIZE` comparison only executed *after* the whole payload was already buffered. The check existed and the config value (`MAX_BODY_SIZE = 1_048_576`) looked authoritative, but it was enforced too late to bound anything: by the time it fired, the allocation it was supposed to prevent had already happened.

This didn't depend on `Content-Length` being honest. `request.body()` consumes the stream to EOF regardless of what (or whether) `Content-Length` declared — a chunked request, an HTTP/2 request, or a client that simply omits the header all hit the same code path with no size signal available before the read starts. (A separate, narrower question — whether a malformed/non-numeric `Content-Length` could itself cause problems — was already checked and disproved during an earlier fix, C5: uvicorn's own parser rejects a non-numeric `Content-Length` with its own 400 before the handler ever runs. That's not what this issue is about; this is about the *size* check, not header parsing.)

`docs/issues/000-known-gaps.md` had this fully root-caused and scoped as "M2 (app-level half)" but recorded it as deliberately deferred rather than implemented, reasoning that the equivalent ingress-level cap (Part 2, `docs/issues/016`) was defense-in-depth. That ingress cap is real and still valuable, but it isn't a substitute for the app being able to defend itself — anything that talks to the app process directly (in-cluster traffic, a misconfigured ingress, local dev without the ingress in front) had no bound at all.

## Production Impact

**Reachable pre-auth.** The size check in `stream()` runs *before* HMAC signature verification:

```python
body = await _read_body_within_limit(request)
if body is None:
    return _outcome_response(
        request, "rejected_too_large", {"error": "payload too large"}, 413
    )

secret: bytes = request.app.state.hmac_key
redis_client = request.app.state.redis

signature = request.headers.get("X-Grd-Signature", "")
if not signature:
    return _outcome_response(
        request, "rejected_signature_missing", {"error": "invalid signature"}, 401
    )
if not verify_signature(secret, body, signature):
    return _outcome_response(
        request, "rejected_signature_invalid", {"error": "invalid signature"}, 401
    )
```

(`proxy.py:269-286`.) The body is read and buffered before any signature check — an unauthenticated caller with no valid HMAC key can drive this path with nothing but network access to `/stream`. Under the old code, that meant an unauthenticated caller could force the process to buffer an arbitrarily large payload into memory on every request, with no per-request cost to the caller beyond bandwidth. One request is a non-issue; a small number of concurrent large/slow-trickling requests against a 128Mi-limited pod (`deploy/helm/pokeproxy/values.yaml`) is a real OOM-kill vector, and it doesn't require guessing the HMAC secret first.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Where to enforce | Pre-check `Content-Length` header only, before reading · **stream the body incrementally via `request.stream()`, counting bytes and aborting once the running total exceeds `MAX_BODY_SIZE`** | **incremental stream check** — a `Content-Length`-only pre-check is trivially bypassed by a chunked request or one that simply omits the header, which is exactly the gap this issue is about. Only reading-while-counting is correct regardless of what the client declares |
| Relationship to the ingress cap | Treat the Part 2 ingress `Middleware` cap (`docs/issues/016`) as sufficient, leave the app unfixed · **fix the app-level check too, keep the ingress cap as an independent layer** | **both** — matches the reasoning already on record in `docs/issues/016`'s own tradeoffs section: "the app-level streaming fix... remains unimplemented" was explicitly called out there as a gap the ingress fix does not close |

## Decision

Enforce the limit while streaming, not after buffering. `request.stream()` yields chunks as they arrive over the wire; summing chunk lengths and bailing out the moment the running total exceeds `MAX_BODY_SIZE` means an oversized request never gets fully materialized in memory, however it declares (or fails to declare) its length.

## Implementation

New helper, used in place of the old `request.body()` call:

```python
async def _read_body_within_limit(request: Request) -> bytes | None:
    """Read the request body, returning None once it exceeds MAX_BODY_SIZE.

    Enforced while streaming rather than after `request.body()`, so a chunked
    or Content-Length-less request cannot buffer an arbitrary payload into
    memory before the limit is checked.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_SIZE:
            return None
        chunks.append(chunk)
    return b"".join(chunks)
```

(`proxy.py:229-243`.) `stream()` calls it first, before touching the signature header, and returns 413 immediately on `None`:

```python
body = await _read_body_within_limit(request)
if body is None:
    return _outcome_response(
        request, "rejected_too_large", {"error": "payload too large"}, 413
    )
```

(`proxy.py:269-273`.) Outcome accounting (`rejected_too_large`, status `413`) is unchanged from before this fix — only *when* the limit is enforced changed, not the observable contract for a client or for the metrics/logging pipeline.

## Verification

Full suite: **122 passing**, `ruff check .` clean.

Existing tests exercising this exact path (both already asserted the 413/outcome contract; neither needed to change for this fix since the external contract didn't change):
- `app/tests/test_logging.py::test_payload_too_large_outcome` — 1,048,577-byte body, asserts `response.status_code == 413` and the access log's `outcome == "rejected_too_large"`.
- `app/tests/test_metrics.py::test_rejected_too_large_is_counted` — same oversized body, asserts `pokeproxy_requests_total{outcome="rejected_too_large", status="413"} == 1`.

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| No test specifically regression-tests the *streaming* behavior itself (i.e., proves the process never allocates the full oversized payload) | The two tests above prove the external contract (413 + correct outcome label) for an oversized body with a normal `Content-Length`, which the old buggy code would also have passed for a body only marginally over the limit. Proving the memory-bounding property itself would need a lower-level harness (a fake ASGI transport that fails/hangs if read past a certain byte count) that doesn't exist in this suite today — not fabricating a claim of coverage that isn't there |
| The ingress-level cap (`docs/issues/016`) remains a separate layer, not superseded by this fix | Intentional — matches the defense-in-depth reasoning already on record there. This fix means the app is no longer solely dependent on the ingress layer for this protection, not that the ingress layer is now redundant |
