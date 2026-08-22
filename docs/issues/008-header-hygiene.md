# H2 + H3 — Headers relayed in both directions with no real hygiene

**Severity:** High (H2, H3) · **Wave:** 4 · **Status:** Fixed
**Files:** `app/src/pokeproxy/proxy.py`

## Problem

Two related gaps, one per direction:

- **H3 (request → downstream):** every client header was forwarded downstream except a fixed 10-name blocklist (`STRIP_HEADERS`). A blocklist is a bet that nothing was missed — standard hop-by-hop headers like `Connection`, `Upgrade`, `TE`, `Trailer`, `Keep-Alive` weren't in it, so client-side connection-management headers leaked through to a hop they don't apply to.
- **H2 (downstream → client):** the downstream response's headers were copied to the client verbatim (`dict(resp.headers)`). `httpx` transparently decompresses `gzip`/`deflate`/`br` bodies but leaves the original `Content-Encoding` header on the response object, so a compressed downstream response would relay a `Content-Encoding` header describing a body that's already been decoded — the client would try to decode it again and fail. `Content-Length`, `Transfer-Encoding`, and `Connection` were relayed the same way, describing the downstream↔proxy hop as if it were the proxy↔client hop.

## Production Impact

Intermittently broken/corrupted response bodies when a downstream service compresses its responses (the `Content-Encoding` case — silent until someone enables compression downstream, then every response fails to parse). Header leakage in both directions makes the proxy's own framing ambiguous to whatever sits on either side of it.

## Options Considered

| Direction | Options | Chosen |
|---|---|---|
| Request → downstream | corrected/complete hop-by-hop blocklist · **allowlist** (forward nothing unless named) | **allowlist** — the proxy already builds every header downstream actually needs (`Content-Type`, `X-Grd-Reason`, `X-Request-ID`); nothing in this system currently reads a client-original header downstream (confirmed: `mock_service` only reads `X-Grd-Reason`, which the proxy generates itself) |
| Response → client | allowlist · **corrected hop-by-hop blocklist** | **corrected blocklist** — the response is inside the trust boundary (from a configured downstream URL, not the client); stripping the standard RFC 7230 hop-by-hop set plus `Content-Length`/`Content-Encoding` preserves whatever business headers a legitimate downstream chooses to set, rather than silently dropping ones we didn't think to allowlist |

## Decision

**`ALLOWED_FORWARD_HEADERS`** replaces `STRIP_HEADERS` — currently empty, so no original client header reaches downstream; the proxy's own three headers are always set regardless of what the client sent. The set is a real, visible mechanism (not dead code) that stays a one-line change if a future need arises (e.g., a tracing header) — not built speculatively now.

**`HOP_BY_HOP_RESPONSE_HEADERS`** — the standard RFC 7230 hop-by-hop set (`Connection`, `Keep-Alive`, `Proxy-Authenticate`, `Proxy-Authorization`, `TE`, `Trailer`, `Transfer-Encoding`, `Upgrade`) plus `Content-Length` and `Content-Encoding` (framing/encoding headers that describe the downstream↔proxy hop specifically, not general hop-by-hop headers but wrong to relay for the same reason). Everything else on the downstream response passes through unchanged.

## Implementation

`_build_forward_headers` flips its filter predicate from "not in STRIP_HEADERS" to "in ALLOWED_FORWARD_HEADERS" — same shape, opposite default. New `_forwardable_response_headers(headers: httpx.Headers) -> dict[str, str]` filters `resp.headers` before they're passed into the client-facing `Response`, called at the one place the forwarded response is constructed.

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13).

| Check | Result |
|---|---|
| New tests (`test_headers.py`) | **6 passed** — no client header reaches downstream by default (unit); the proxy's own three headers are always set regardless of input (unit); hop-by-hop response headers are stripped (unit); a non-hop-by-hop response header passes through (unit); end-to-end through `TestClient` confirms `Authorization`/`Cookie` sent by the client never arrive at a mocked downstream while `X-Grd-Reason` does; end-to-end confirms a mocked downstream's `Connection` header never reaches the client while a custom header does |
| Full suite | **73 passed** (was 67) |
| `ruff check .` | clean |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| No original client header currently reaches downstream, including ones a future integration might want (tracing headers, `User-Agent`) | Deliberate, not accidental — nothing in this system needs one today; extending `ALLOWED_FORWARD_HEADERS` is a one-line change when a real need appears, not a redesign |
| Response allowlist was considered and rejected in favor of a corrected blocklist | The response comes from a configured, trusted downstream URL — an allowlist there risks silently dropping legitimate business headers we didn't think to name; a blocklist is the right default when the far side is trusted and the near side (the client) is not |
