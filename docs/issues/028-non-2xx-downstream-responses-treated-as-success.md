# Non-2xx downstream responses were counted and cached as success

**Severity:** Major · **Wave:** final review · **Status:** Fixed
**Files:** `app/src/pokeproxy/proxy.py`, `app/src/pokeproxy/metrics.py`, `deploy/helm/pokeproxy/dashboards/pokeproxy-overview.json`, `deploy/helm/pokeproxy/templates/pokeproxy/prometheusrule.yaml`

## Problem

`_forward_with_retry` (`proxy.py:112-141` pre-fix) returned whatever `client.post()` gave back. httpx does not raise on a 4xx/5xx response — that's an explicit design choice on httpx's part (`raise_for_status()` is opt-in). So a downstream `503` came back as a normal, non-exceptional `httpx.Response`, and `_forward_request`'s success branch treated it exactly like a `200`: incremented `downstream_requests_total{result="success"}`, set `outcome="forwarded"`, and handed it to `cache_response`.

## Production Impact

Two independent problems from one root cause:

1. **Wrong error-rate accounting.** Part 4's dashboards and the `PokeProxyHighServerErrorRate` alert reason about `downstream_requests_total{result=...}`. A downstream outage that responds with 503 instead of dropping the connection was invisible on this metric — it looked identical to healthy traffic.
2. **A transient downstream error got cached and replayed for `CACHE_TTL_SECONDS`.** Once one duplicate payload got a 503 cached, every duplicate of that payload got the same stale 503 replayed for up to 300s (default TTL), even after downstream fully recovered — the dedup layer (issue 010) turned a one-request blip into a sustained one.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| How to detect failure | call `resp.raise_for_status()` and catch `httpx.HTTPStatusError` · **check `200 <= resp.status_code < 300` explicitly** | **explicit range check** — `raise_for_status()` would fold this into the existing `except httpx.HTTPError` branch, which returns a synthetic 502 body instead of relaying what downstream actually said. The proxy's contract is to relay the downstream answer; only the accounting/caching decision needed to change |
| New outcome name | reuse `downstream_error` (already used for the connection-failure/exception path) · **new `downstream_non_2xx`** | **new name** — `downstream_error` already means "the proxy never got a response to relay" (synthetic 502 body, fixed status). A relayed non-2xx is a different failure mode (whatever downstream actually returned) and collapsing them into one outcome would make the two indistinguishable from logs/dashboards without also checking `status` |
| Caching | cache non-2xx with a shorter TTL · **never cache non-2xx** | **never cache** — matches the existing rule from issue 010 that only a genuine downstream answer is cache-worthy; a 5xx is exactly the kind of transient failure that dedup should not amplify |

## Decision

`_forward_request`'s success branch now checks `is_downstream_success = 200 <= resp.status_code < 300` before deciding the outcome:
- 2xx: unchanged — `result="success"`, `outcome="forwarded"`, cached.
- non-2xx: `result="error"` (same label already covered by the dashboard's `result=~"error|timeout"` error-rate query and the `PokeProxyHighServerErrorRate` alert's use of `status=~"5.."`), `outcome="downstream_non_2xx"`, **not** cached. The actual downstream status/body/headers are still relayed to the client unchanged — only the accounting and caching decision changed.

Grafana/alerting: the `PokeProxyHighServerErrorRate` alert's description text listed the outcomes responsible for a 5xx (`internal_error`, `downstream_error`, `downstream_timeout`) — updated to also name `downstream_non_2xx` (this fix) and `downstream_response_too_large` (issue 030) so the annotation stays accurate. The alert's PromQL itself (`status=~"5.."`) needed no change — it already fires correctly regardless of outcome name.

## Implementation

`proxy.py:_forward_request` — single `is_downstream_success` branch replaces the unconditional success path; caching call moved inside the 2xx branch. `deploy/helm/pokeproxy/templates/pokeproxy/prometheusrule.yaml` — annotation text only, no expr change.

## Verification

Run via Docker (`ghcr.io/astral-sh/uv:python3.13-bookworm-slim`, whole repo mounted): `ruff check .` clean, full suite passing (122 tests, was 111).

New tests:
- `test_dedup.py::test_non_2xx_downstream_response_is_not_cached_and_the_next_duplicate_retries` — a 503 then a 200 on retry proves the 503 wasn't cached.
- `test_metrics.py::test_non_2xx_downstream_response_is_counted_as_a_downstream_error` — asserts `outcome="downstream_non_2xx"`/`status="503"` on `requests_total` and `result="error"` on `downstream_requests_total`.

## Tradeoffs / Remaining Risk

`downstream_non_2xx` relays whatever status downstream sent, including a downstream 4xx (client-side business error, not really "our" error). It's still labeled `result="error"` on `downstream_requests_total` — that metric is about "did the forward attempt succeed," not "was it downstream's fault," so a operator reading `pokeproxy_downstream_requests_total{result="error"}` needs to check `status` on `requests_total` to tell a 503 outage apart from a downstream 404. Splitting further (e.g. a `client_error`/`server_error` result split) would add label cardinality for a distinction Part 4's dashboards don't currently need — left as a possible follow-up if a real downstream starts returning 4xx often enough to matter.
