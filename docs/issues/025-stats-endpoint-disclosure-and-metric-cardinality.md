# `/stats` disclosed internal topology and its dict pattern didn't scale to Prometheus labels (M5, L4)

> Written under the token-economy rule (`CLAUDE.md`): maximum information in minimum cost.
> Tables over prose. Keep evidence, exact numbers, `file:line` refs and honest uncertainty;
> cut restatement, hedging and filler. Terse is the goal; vague is a failure.

## Problem

Two gaps identified during Part 1's review and deferred to Part 4 (`docs/issues/000-known-gaps.md`):

- **M5** — `/stats` (`main.py:200`, pre-Part-4) was unauthenticated and returned `EndpointStats` keyed by the raw downstream URL from `config/rules.json`, disclosing internal routing topology to anyone who could reach the service.
- **L4** — `StatsRegistry.get()` (`stats.py:47`, pre-Part-4) called `self.endpoints.setdefault(url, EndpointStats())` — bounded today only by the rules file, an unbounded-cardinality pattern that would have been carried straight into Prometheus label sets if instrumentation had reused the same key.

A third, previously-undocumented bug surfaced while building the replacement: `proxy.py`'s `_forward_request` set `request.state.outcome` to `forwarded`/`downstream_timeout`/`downstream_error` at three call sites but never called `stats.record_outcome()` on any of them — only the reject/no-match/duplicate paths did. `/stats`'s `outcomes` map was silently missing its three most important entries.

## Production Impact

M5: an unauthenticated endpoint leaking which downstream URLs exist is a reconnaissance surface, however minor at this service's scale. L4: a URL-keyed label on a Prometheus metric is the canonical cardinality-explosion mistake — harmless while `rules.json` is small and operator-controlled, a real problem the moment a rule's URL becomes templated or the file grows. The missing-outcomes bug meant `/stats` under-reported the majority of real traffic — an operator trusting the endpoint during an incident would have seen no `forwarded` count and no downstream error/timeout signal at all.

## Options Considered

1. Keep `/stats`, add auth, key by rule name instead of URL. Preserves the endpoint, fixes M5 and L4 narrowly.
2. Delete `/stats` outright, replace with Prometheus metrics that never carry `url` as a label, driven from a single recording seam.

## Decision

Option 2, user-approved. `/stats`'s `EndpointStats` precomputed `error_rate`/`avg_response_time` **per process** — with `replicaCount: 2`, there is no query that recombines two pods' precomputed ratios into a fleet number; the endpoint was structurally wrong for more than one replica regardless of its auth/cardinality issues. Patching it in place would have fixed the two named gaps while leaving that structural problem and the missing-outcomes bug intact.

## Implementation

- `app/src/pokeproxy/stats.py` and `app/tests/test_stats.py` deleted.
- `app/src/pokeproxy/metrics.py` (new): `prometheus-client` `Counter`/`Histogram`/`Gauge` definitions on a per-app-instance `CollectorRegistry`.
- `pokeproxy_requests_total{outcome,status}` and `pokeproxy_request_duration_seconds` are recorded from exactly **one** place — the access-log middleware (`main.py:147`), reading `request.state.outcome`, which every terminal path already sets. This is what makes the missing-outcomes class of bug structurally impossible rather than fixed once: there is no per-call-site recording step left to forget.
- `pokeproxy_downstream_requests_total{rule,result}`, `pokeproxy_downstream_duration_seconds{rule}`, `pokeproxy_downstream_retries_total{rule}` — labeled by the matched rule's `reason` string (`proxy.py:143`), never `url`. Bounded by `rules.json`'s length, which is operator config, not request-driven — closing L4 by construction rather than by convention.
- `pokeproxy_cache_operations_total{operation,result}` (`cache.py`) — `operation ∈ {get,set}`, `result ∈ {hit,miss,error}`/`{success,error}`. No URL or key material in any label.
- `/metrics` replaces `/stats`, no authentication added or needed — it discloses aggregate counts by outcome/rule/operation, not URLs, matching what a Prometheus scrape target normally exposes.

## Verification

`ruff check .` clean. Full suite `pytest -q`: 111 passed, including new `test_metrics.py` (13 tests, one real HTTP request per terminal outcome asserting the corresponding metric increments exactly once via `CollectorRegistry.get_sample_value()`) and 5 new cache-operation tests in `test_cache.py`. Live-verified in the dev k3d cluster: drove 297+ real requests spanning all 3 rules and 4 reject/no-match outcomes through the deployed app, queried Prometheus directly and confirmed every metric family populated with real, non-zero values, no `url` label anywhere in the exposition output (confirmed by inspecting `/metrics` output directly).

## Tradeoffs / Remaining Risk

- `/metrics` sits on the same port as the app (8000), not a separate listener — deferred (see `docs/planning/part-04-observability.md` D4): the only external exposure is the Ingress, routed `path: /stream` exactly, so `/metrics` was never externally reachable regardless of port; a second listener would be real work closing a hole that doesn't exist today.
- The request-duration histogram is unlabeled (no `outcome` dimension) — a 401 (sub-millisecond) and a slow forward share one histogram, which dilutes p99 slightly. Documented fallback (`outcome_class`, 5 values) scoped but not built without evidence it's needed.
