# Part 4 — Observability

## What I am actually solving

The service already has structured JSON logs (Part 1, H2/H3) and a per-pod `/stats` endpoint. Neither answers "is this service healthy right now" for a fleet: logs aren't aggregatable at a glance, and `/stats` precomputes ratios per pod that cannot be recombined across the 2 replicas — you cannot average two pods' `error_rate` and get the fleet's. Part 4 replaces `/stats` with Prometheus-compatible metrics, ships a monitoring stack into both clusters, and answers the health question with one dashboard and one alert that's provably armed.

Three things drove the design, all found by reading the code rather than assumed:

1. **"Error rate" needs a definition, not a formula.** A 401 on a bad HMAC signature is the service working correctly. `status >= 400` as "error" would show 100% error the moment a scanner probes the endpoint. Every server-fault outcome (`internal_error`/500, `downstream_error`/502, `downstream_timeout`/504) is 5xx; no client-fault outcome is. `status=~"5.."` is exactly the failure set, for free.
2. **`/stats` is structurally wrong for >1 replica**, not just supersedable. [stats.py:31](../../app/src/pokeproxy/stats.py:31) computes `error_rate` and `avg_response_time` per process; there is no query that turns two of those into a fleet number. Combined with M5 (discloses internal downstream URLs, unauthenticated) and L4 (unbounded-cardinality key pattern), the call is delete, not port.
3. **A live bug in the thing I'm replacing.** [proxy.py:133](../../app/src/pokeproxy/proxy.py:133), `:151`, `:158` set `request.state.outcome` to `forwarded`/`downstream_timeout`/`downstream_error` but never call `stats.record_outcome()` — only the reject/no-match/duplicate paths do. Today's `/stats` `outcomes` map is silently missing the three outcomes that matter most. Driving every counter from the single access-log middleware ([main.py:161](../../app/src/pokeproxy/main.py:161)) — the one place `request.state.outcome` is already read — makes this class of bug structurally impossible instead of fixed once.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **`prometheus-client`** (official) + ~25 lines of hand-written middleware, not `prometheus-fastapi-instrumentator` or OpenTelemetry | The entire value is in the `outcome` label, which only our code knows. Auto-instrumentation labels by handler/method/status and can't see it. OTel's value is traces/portability — we're adding metrics, not exercising either; documented as the natural next step if request-level tracing is ever wanted |
| D2 | Delete `/stats`, `stats.py`, `StatsRegistry` outright, not keep alongside `/metrics` | Every consumer of `/stats` (a human hitting it manually — nothing in the codebase depends on it) is better served by `/metrics`, and keeping both means keeping the bug in D-point-3 above alive as dead weight. User-approved |
| D3 | Label the forward-path metrics by **`rule` (the rule's `reason` string), never `url`** | Closes L4 (unbounded-cardinality-by-URL pattern) and half of M5 (internal downstream topology disclosure) in the same choice that RED metrics needed anyway. Bounded by `rules.json` length — operator config, not traffic |
| D4 | `/metrics` stays on the app port (8000), not a second listener | The only external exposure is the Ingress, routed `path: /stream, pathType: Exact` ([ingress.yaml:25](../../deploy/helm/pokeproxy/templates/pokeproxy/ingress.yaml:25)) — `/metrics` is not externally reachable today regardless of port. A second ASGI listener is real work for a hole that doesn't exist yet; documented as hardening if the topology ever changes |
| D5 | Request duration histogram is **unlabeled**, with buckets tuned to this service (`0.001…10, +Inf`) | A labeled histogram (by outcome) multiplies series 5-13x for a service this small. Known cost: 401s (sub-ms) dilute the p99 alongside slow forwards. Documented fallback (`outcome_class`, 5 values) if that turns out to matter, not built without evidence |
| D6 | Monitoring stack: **`kube-prometheus-stack`**, installed imperatively (same layer as Argo CD/sealed-secrets today), into **both** clusters | Gives `ServiceMonitor`/`PrometheusRule` as CRDs — scrape config and alert rules live in the app chart, in git, Argo-reconciled in prod. A hand-rolled Prometheus+Grafana pair would mean hand-maintained scrape config and non-GitOps alerts, against the grain of Part 3. Both clusters because a config path that only ever runs in prod rots; `MONITORING=false` escape hatch for fast dev iteration. User-approved |
| D7 | The app-side objects (ServiceMonitor, PrometheusRule, dashboard ConfigMap) live in the **app chart**, gated `monitoring.enabled` | Consistent with everything else in `deploy/helm/pokeproxy` — reconciled by Argo in prod, by `helm upgrade` in dev |
| D8 | `emptyDir` storage, ~6h retention | A PVC that doesn't survive a k3d node is theater. Explicitly wrong for a real deployment; documented, not solved here |
| D9 | Alertmanager **kept**, not dropped | The assignment asks for an alert "ready to fire," which is only demonstrable — not just asserted — if something can transition pending→firing and be observed doing so |

## Proposed metrics

`prometheus-client`'s default collectors, free: `process_cpu_seconds_total`, `process_resident_memory_bytes`, `process_open_fds`, `python_gc_*`, `python_info`.

Verified single-process before relying on the in-process default registry: [`__main__.py:24`](../../app/src/pokeproxy/__main__.py:24) calls `uvicorn.run()` with no `workers` argument, so there is exactly one process per pod and `prometheus_client`'s multiprocess mode (the standard Python-metrics footgun) does not apply. `replicaCount: 2` gives two independent scrape targets — per-pod series, aggregated in PromQL, which is what we want.

Custom:

| Metric | Type | Labels | Cardinality | Purpose |
|---|---|---|---|---|
| `pokeproxy_requests_total` | Counter | `outcome`, `status` | ~12 | R+E of RED — the full outcome taxonomy. `status` isn't redundant with `outcome`: `forwarded` passes the downstream's own code through, so `outcome=forwarded,status=500` is real and otherwise invisible |
| `pokeproxy_request_duration_seconds` | Histogram | — | 13 buckets | D of RED — total latency as the caller experiences it |
| `pokeproxy_downstream_requests_total` | Counter | `rule`, `result` | 3×3 | Per-rule forward volume/result, `result` ∈ `{success, timeout, error}` |
| `pokeproxy_downstream_duration_seconds` | Histogram | `rule` | 3×13 | The "us or them" metric — total latency minus this is our own overhead |
| `pokeproxy_downstream_retries_total` | Counter | `rule` | 3 | Leading indicator — climbs before the 10s deadline is exhausted and a caller sees a 504 |
| `pokeproxy_cache_operations_total` | Counter | `operation`, `result` | 5 | See below |
| `pokeproxy_build_info` | Gauge (=1) | `revision`, `version` | 1 | Which sha is serving — correlates a regression with a deploy on the same dashboard |

Total new series per pod: ~90. Two replicas: ~180. Trivial at any Prometheus scale.

**`pokeproxy_cache_operations_total` is the highest-value non-obvious metric here.** [cache.py:19](../../app/src/pokeproxy/cache.py:19) catches `RedisError` and returns `None` — a cache error is indistinguishable from a cache miss, by design (M1: Redis must not gate readiness). That correct resilience decision creates a completely silent failure mode: Redis dies, every request becomes a miss, dedup stops, downstream load multiplies by the duplicate ratio, and no existing signal names the cause. `{operation="get",result="error"}` is the only place this becomes visible.

### Cardinality decisions

- `rule`, never `url` (D3).
- No `path` label — one real path; probe paths (`/health`, `/ready`, `/metrics`) excluded from instrumentation entirely, same as from access logging, so kubelet's sub-ms probes don't flatten the histogram.
- No `method` — POST only.
- No `request_id`, pokemon `name`, or `type_one` — unbounded/traffic-driven; these stay in structured logs, which already carry `request_id`.
- Dropped from `EndpointStats` on purpose: `bytes_sent`/`bytes_received` (vanity at this payload size), `avg_response_time` (a histogram strictly dominates an average), `error_rate` (see D-point-2 above).

### Runtime/K8s vs custom metrics — division of labor

| Question | Source | Why not app-level |
|---|---|---|
| About to be OOMKilled? | cAdvisor `container_memory_working_set_bytes` | `process_resident_memory_bytes` ≠ working set; the kernel acts on working set |
| CPU-throttled? | cAdvisor `container_cpu_cfs_throttled_periods_total` | Invisible to the process; throttling hurts more than raw CPU% shows |
| Pods restarting / rollout stuck? | kube-state-metrics | A crashed pod scrapes nothing — absence of a metric is not a metric no app-level code can report |
| Event loop starved / fd leak? | `prometheus_client` default collectors | Free, standard |
| Why did this specific request fail? | app metrics | Only the app knows |

## Where `/metrics` lives

Same port as the app (D4). New NetworkPolicy: ingress to `pokeproxy:8000` from the `monitoring` namespace, alongside the existing `allow-ingress-to-pokeproxy` rule scoped to kube-system.

## Dashboard — "is this service healthy right now"

One dashboard, four rows, read in on-call order.

**Row 1 — verdict** (stat panels): server error rate, request rate, p99 latency, pods ready/desired (the one panel app metrics structurally can't provide — kube-state-metrics only).

**Row 2 — RED detail**: request rate by outcome (stacked — the single highest-information panel, the whole taxonomy at a glance), latency p50/p90/p99 with downstream p99 overlaid (the "us or them" panel).

**Row 3 — dependencies**: downstream rate/error-rate/retry-rate by rule; cache hit rate + Redis error rate (the silent-failure panel).

**Row 4 — resources & version**: CPU vs limit + throttled-period ratio; memory working set vs limit; restart count + `pokeproxy_build_info` revision.

## Alerts

**Primary — the one the assignment asks for:**

```yaml
alert: PokeProxyHighServerErrorRate
expr: |
  sum(rate(pokeproxy_requests_total{status=~"5.."}[5m]))
    / sum(rate(pokeproxy_requests_total[5m])) > 0.05
  and sum(rate(pokeproxy_requests_total[5m])) > 0.1
for: 10m
labels: {severity: page}
```

| Choice | Justification |
|---|---|
| `status=~"5.."` | Not `>=400` — 401 is correct rejection, not failure. Every server-fault outcome is 5xx, no client-fault outcome is |
| `0.05` (5%) | Above the noise the retry policy already absorbs (3 attempts / 10s budget swallow a transient downstream blip without ever producing a 5xx). Below what a caller notices. Not 1%: at this service's realistic volume, 1% is ~1 request — it flaps. Not 0%: pages on a single transient 502, gets muted, and a muted alert is worse than none |
| `for: 10m` | Must exceed the retry budget (`forward_deadline_seconds: 10s`) by orders of magnitude — sustained, not a bad minute. Must also exceed a normal rolling deploy (`maxUnavailable: 0`, 2 replicas, 30s grace + 5s preStop) so shipping never pages. 10m ≈ two full 5m rate windows of genuine breakage |
| `and ... > 0.1` | Guards the classic ratio-alert failure mode: near-zero traffic makes the ratio NaN or flappy. Honest caveat: this floor also makes a total traffic *stoppage* invisible to this alert — that's what `PokeProxyTargetsDown` below is for |

**Two more, closing holes the primary alert leaves:**

```yaml
alert: PokeProxyCacheBackendErrors
expr: sum(rate(pokeproxy_cache_operations_total{result="error"}[5m])) > 0
for: 5m
labels: {severity: warning}
```
Exists because Redis is deliberately non-fatal (M1) and nothing else surfaces its failure. `> 0` because steady state is exactly zero. `warning`, not `page`: the service still serves correctly, just without dedup.

```yaml
alert: PokeProxyTargetsDown
expr: absent(up{job="pokeproxy"} == 1)
for: 5m
labels: {severity: page}
```
Every ratio alert is silent when there are zero scrape targets. A crashed pod emits nothing.

### Deliberately not alerted on

**`no_rule_matched` rate.** Entirely a function of the traffic mix and `rules.json` — neither is something the service controls or can be "wrong" about. A healthy PokeProxy fed a stream with no Fire-types correctly matches and forwards nothing. A spike means the upstream population shifted or someone edited rules — a product/config question for business hours, not a 3AM action. Any threshold here is a guess about traffic composition dressed as an SLO. Stays prominent on the dashboard (row 2, panel 5) so it's visible while debugging something else, just not paged on.

**Runner-up: cache hit rate.** Also traffic-shaped (the load generator's fixed payload set already skews it, per the WORKLOG's M4 note), and more importantly a lagging, ambiguous proxy for "Redis is broken" when the direct cause (`cache_operations_total{result="error"}`) is already alertable. Alert on the cause, graph the symptom.

## Tradeoffs

| Trade | Accepted cost |
|---|---|
| `kube-prometheus-stack` | ~10 workloads on a laptop cluster, for CRD-declarative scrape/alert config that fits GitOps |
| `prometheus-client` + own middleware | ~25 lines to maintain, for zero magic and full control of the label set |
| `/metrics` on the app port | Reachable by anything that can reach :8000 in-namespace (today: kube-system + monitoring, by NetworkPolicy); second-listener hardening documented, deferred |
| `emptyDir` + ~6h retention | Metrics vanish on pod restart — correct for a laptop, explicitly wrong for real; PVC + remote-write is the real answer |
| Unlabeled request histogram | 401s dilute p99; fix scoped (`outcome_class`), not built without evidence |
| No tracing, no log aggregation | Structured JSON logs with `request_id` already exist and are `kubectl logs`-greppable; Loki/Tempo would be a second and third stack, out of scope |
| Redis and mock-downstream not scraped | Real work for near-zero assignment value; Redis health covered indirectly by `cache_operations_total` and kube-state-metrics |

## Later-part implications

Metric and label names are the contract Part 5's bootstrap verification checks against. `rule`-not-`url` labeling closes M5 and L4 in this part rather than deferring them again.

## Implementation steps

| # | Step | Verification |
|---|---|---|
| 1 | **Instrument the app.** `prometheus-client` dependency, new `metrics.py`, wire the access-log middleware + downstream/cache call sites, expose `/metrics`, delete `/stats`/`stats.py`/`StatsRegistry`, new tests | `ruff check`, `pytest`, scrape locally, one increment per outcome per request |
| 2 | **Fix the load generator's dedup ceiling.** Randomize `number` (as `e2e_check.py` already does) so payload bytes vary — prerequisite for step 4: today 12 fixed payloads → 12 distinct hashes → everything past the 12th request is `duplicate_suppressed`, and a dashboard demo reads as a dead service. `number` isn't matched by any rule, so routing is unaffected | Run generator, confirm sustained `forwarded` rate over time |
| 3 | **Deploy the stack.** `kube-prometheus-stack`, trimmed values, into dev via `deploy.sh` | Grafana/Prometheus up; kube-state-metrics and cAdvisor targets UP |
| 4 | **Wire scraping.** `ServiceMonitor` + NetworkPolicy (monitoring → pokeproxy:8000), under `monitoring.enabled` | pokeproxy target UP in Prometheus; every metric above queryable with real values |
| 5 | **Dashboard.** Grafana-sidecar-labeled ConfigMap in the chart | All 11 panels render non-empty under load-generator traffic |
| 6 | **Alerts.** `PrometheusRule` in the chart | Force the condition (scale mock-downstream to 0), watch pending→firing in Alertmanager |
| 7 | **Prod.** Enable in `bootstrap-prod.sh` + `deploy/envs/prod/values.yaml`, verify under Argo CD | Synced/Healthy, target UP in prod |
| 8 | **Docs.** Issue write-ups closing M5+L4, `app/README.md`, WORKLOG, `AI_WORKFLOW.md` | — |
