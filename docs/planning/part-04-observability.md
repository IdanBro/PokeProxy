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

**Row 1 — verdict** (stat panels): server error rate, request rate, p99 latency, pods ready/desired (the one panel app metrics structurally can't provide — kube-state-metrics only), PokeProxy alerts firing, targets up.

**Row 2 — RED detail**: request rate by outcome (stacked — the single highest-information panel, the whole taxonomy at a glance), latency p50/p90/p99 with downstream p99 overlaid (the "us or them" panel), server error rate by pod (per-pod breakdown so one bad-but-not-dead replica isn't invisible behind a fleet-wide sum).

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
labels: {severity: critical}
```
`critical`, not `page` (A-4 fix, 2026-08-25): the deployed Alertmanager's inhibit rules are keyed on `severity = critical` / `=~ warning|info` — `page` matched none of them and could neither inhibit nor be inhibited. `critical` reuses the chart's built-in vocabulary instead of adding new Alertmanager routing config.

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
labels: {severity: critical}
```
Every ratio alert is silent when there are zero scrape targets. A crashed pod emits nothing. `critical`, not `page` — same A-4 fix and reasoning as the primary alert above.

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

---

## Requirement audit — 2026-08-25

Second pass against `README_HOME_ASSIGNMENT.md` Part 4, run live against the dev cluster (`k3d-pokeproxy`, 6/6 monitoring pods, 4/4 app pods, ~8.5 req/s of load-generator traffic) rather than from this document's own record. Method: query Prometheus's HTTP API directly (`/prometheus/api/v1/{query,rules,alerts,targets,series,alertmanagers}` via port-forward), execute one panel query through Grafana's `/api/ds/query` to prove the datasource plumbing, enumerate every metric name the `pokeproxy` job actually exposes, and probe the ingress from outside the cluster.

**Working tree state at audit time:** `deploy/monitoring/values.yaml`, `scripts/deploy.sh`, `deploy/README.md` carry uncommitted changes (path-based Ingress for Grafana + Prometheus; monitoring install moved before the app deploy). Two of the findings below are caused by that in-flight work, not by what merged in PR #9.

### What verified clean

| Claim | Evidence |
|---|---|
| Metrics are really scraped | `up{job="pokeproxy"}` = 1 on both pods (`10.42.0.19:8000`, `10.42.0.20:8000`), `interval 15s`, `lastError` empty. 13 of 14 cluster targets up |
| Every custom metric carries real data | `requests_total` across 5 outcomes (forwarded 702, no_rule_matched 684, duplicate_suppressed 1, both reject outcomes 2 each), `downstream_requests_total` across all 3 rules, `cache_operations_total` get/set, `request_duration_seconds_count` 1391, `build_info{revision="ed19bd2",version="0.1.0"}` |
| Dashboard queries resolve | 22 of 24 panel queries return live series. p99 = 0.190s, downstream p99 = 0.119s, cpu 0.054 vs limit 1, working set 137 MiB vs limit 512 MiB, throttled ratio 0.0004, restarts 0 on both pods |
| Grafana actually serves it | Dashboard `uid: pokeproxy-overview` present via the sidecar; datasource `Prometheus` (uid `prometheus`, default); panel-2 query executed through `/api/ds/query` returned a frame (`0`) — the `${datasource}` to real-datasource path works end-to-end, not just against Prometheus directly |
| Alert rules load and evaluate | Group `pokeproxy`, 3 rules, `health: ok`, `lastError` empty, `evaluationTime` 0.0009s, interval 30s, all `inactive`. Alertmanager discovered (`10.42.0.14:9093`) |
| Alert expressions are structurally capable of firing | Shape-equivalence probes (same expression, selector swapped for one that currently has data): ratio-plus-floor form returns `0.495` — proving `>` binds tighter than `and` and both `sum()` sides label-match on `{}`; `absent(up{job="pokeproxy-does-not-exist"} == 1)` = 1; `absent(up{job="kube-prometheus-stack-grafana"} == 1)` = 1 — i.e. `absent()` fires both when the job is unknown *and* when targets exist but are all down, which is the documented condition |
| `/metrics` is not externally reachable (D4) | `curl http://localhost:8080/metrics` returns 404 through the ingress; `/stream` returns 405. D4's argument still holds |
| Instrumentation is sound in code | `ruff check` clean, `pytest` 111 passed. Single recording seam intact (`main.py:147-181`); `_UNINSTRUMENTED_PATHS` covers `/health`, `/ready`, `/metrics` |
| Cardinality is as planned | 187 series total for the job, 21 distinct names — the plan predicted ~180 |

### "Is PokeProxy healthy right now?"

**Inside the dashboard: yes.** Row 1 is six stat tiles — server error rate (green / yellow at 1% / red at 5%), request rate, p99 (yellow 1s / red 5s), pods ready vs desired, targets up (red below 1). An on-call reads the top strip and stops. That satisfies the requirement.

**Getting to the dashboard: not yet.** Grafana holds 25 dashboards, all in the root folder, no home dashboard configured. "PokeProxy Overview" sits alphabetically between *Node Exporter / USE Method* and *Prometheus / Overview* — the literal "twenty unrelated charts" problem, one level up from the panel layout. See A-8.

### Findings

**BLOCKER**

| # | Finding | Evidence | Fix |
|---|---|---|---|
| A-1 | **The default `process_*` / `python_*` collectors do not exist**, but this document lists them as shipping "free" (§ Proposed metrics) and § Runtime/K8s-vs-custom assigns "Event loop starved / fd leak?" to them. `Metrics.create()` builds a fresh `CollectorRegistry()`; the default collectors live in the module-level `prometheus_client.REGISTRY` and are never registered into it | `{job="pokeproxy", __name__=~"process_.*\|python_.*"}` returns **0 series**. All 21 exposed names are `pokeproxy_*`, `up`, or `scrape_*` | 3 lines in `metrics.py`: `ProcessCollector(registry=registry)`, `PlatformCollector(registry=registry)`, `GCCollector(registry=registry)` (all three accept `registry=`, verified against the installed 0.26.0). Preserves the per-instance-registry test-isolation rationale. Then correct this document either way — a planning artifact naming metrics an interviewer can disprove with one `curl` is worse than the gap itself |
| A-2 | **The uncommitted Grafana sub-path change breaks the stack's own Grafana scrape target**, leaving `TargetDown` permanently firing. `serve_from_sub_path: true` plus `root_url: .../grafana/` makes Grafana 301 `/metrics` to `http://localhost/grafana/metrics`; the chart's ServiceMonitor still scrapes `path: /metrics`. The Prometheus subchart derived its own path from `routePrefix` correctly (`/prometheus/metrics`); the Grafana subchart does not | Target `kube-prometheus-stack-grafana` health `down`, `dial tcp [::1]:80: connect: connection refused`. `TargetDown` **firing** since 22:56, `severity: warning`. A permanently-red alert is exactly the "muted alert is worse than none" failure this document argues against under the 0% threshold | Add `grafana.serviceMonitor.path: /grafana/metrics` to `deploy/monitoring/values.yaml`. Do not commit the sub-path change without it |

**SHOULD FIX**

| # | Finding | Evidence | Fix |
|---|---|---|---|
| A-3 | **The uncommitted Prometheus Ingress exposes the full Prometheus UI and read API unauthenticated**, in dev *and* prod (`bootstrap-prod.sh` step 5 loads the same values file). Same disclosure class as M5, which `docs/issues/025` was just written to close | `http://localhost:8080/prometheus/api/v1/query?query=up` returns **200**; `/api/v1/status/config` returns **200** (full scrape config); `POST /-/reload` returns **200** (lifecycle API on by chart default). Grafana correctly 302s to login | Either drop the Prometheus Ingress and keep port-forward, or put it behind a Traefik basic-auth middleware, or document the exposure explicitly as a laptop-only trade-off with the prod path disabled. Silently shipping it undoes 025 |
| A-4 | **`severity: page` matches nothing in the deployed Alertmanager config.** The chart's own inhibit rules key on `severity = critical` / `=~ warning\|info`; nothing in the stack understands `page`. Two of our three alerts are labelled `page` | Deployed Alertmanager config: `inhibit_rules[].source_matchers: severity = critical`. Our page-severity alerts can neither inhibit nor be inhibited, and any future severity-based route would drop them to the catch-all | Use `critical`, or keep `page` deliberately and add a route/inhibit pair that knows about it. Mixed severity vocabularies in one Alertmanager is the bug, not the word choice |
| A-5 | **Two dashboard series render "No data" rather than 0 under healthy conditions** — the exact failure step 5 fixed for the two verdict panels, still present on panel 10. `prometheus_client` creates a child series only on first `.labels(...)` call, so `downstream_retries_total` and `downstream_requests_total{result=~"error\|timeout"}` have zero series until the first real failure | `sum(rate(pokeproxy_downstream_retries_total[5m])) by (rule)` returns empty. `sum(rate(pokeproxy_downstream_requests_total{result=~"error\|timeout"}[5m])) by (rule)` returns empty. 2 of the 24 panel queries | Pre-initialise the label combinations at startup — the rules are known from `rules.json`, so `for rule in rules: retries.labels(rule=rule.reason)` plus the three `result` values on `downstream_requests_total`. `or vector(0)` cannot be used here: it drops the `by (rule)` grouping |
| A-6 | **The "Pods ready / desired" verdict tile can never look wrong** — its threshold list is a single blue step, so 1-of-2 ready renders identically to 2-of-2 | Panel 5 `thresholds.steps` is `[{color: blue, value: null}]`, versus panel 16 (Targets up) which correctly has `red < 1`, `green >= 1` | Plot `ready / desired` with `red < 1`, or add an override so `ready` colours against `desired` |
| A-7 | **`deploy/README.md`'s Grafana access instructions are broken by the same uncommitted change** and never mention the new Ingress | With `serve_from_sub_path`, `http://localhost:3000/` 301s to `http://localhost/grafana/` — the port is dropped, so the documented port-forward URL dead-ends. `http://localhost:3000/grafana/` works; so does `http://localhost:8080/grafana/` via the new Ingress | Update both URLs in the monitoring section |

**NICE TO HAVE**

| # | Finding | Evidence / fix |
|---|---|---|
| A-8 | Set the PokeProxy dashboard as Grafana's home. 25 dashboards, all root-folder, no home configured — the on-call's first click is a search box | `grafana.ini: dashboards: default_home_dashboard_path: /tmp/dashboards/pokeproxy-overview.json` (the sidecar's `FOLDER=/tmp/dashboards`, read from the running container's env) |
| A-9 | No panel shows whether a PokeProxy alert is firing — the dashboard and the alerts are separate universes | `count by (alertname, alertstate) (ALERTS{alertname=~"PokeProxy.*"})` is available and populated (confirmed live against the stack's own alerts); one stat or table tile in row 1 |
| A-10 | Every panel aggregates across pods (`sum(...)` with no `by (pod)` except the restart table). One sick replica of two halves the fleet error rate and is individually invisible; "Targets up" only catches a *dead* pod, not a *bad* one | Add `by (pod)` to the outcome panel, or one per-pod error-rate panel in row 2 |
| A-11 | Alert labels are `alertname` plus `severity` only — `sum()` with no `by` strips everything else. Alertmanager `group_by: [namespace]` and the inhibit rules' `equal: [namespace, alertname]` therefore see an empty namespace | Add static `namespace` / `service` labels to the rules |
| A-12 | 31 of 187 series (17%) are `_created` timestamp gauges nobody queries | `PROMETHEUS_DISABLE_CREATED_SERIES=true` in the deployment env, or `prometheus_client.disable_created_metrics()` at startup (both exist in 0.26.0) |
| A-13 | The monitoring stack is CPU-starved on this laptop and Prometheus is already dropping work | `container_cpu_cfs_throttled_periods_total` rate: grafana 1.70/s, node-exporter 0.53/s, grafana sidecars 0.28 + 0.07/s, prometheus 0.22/s. `PrometheusMissingRuleEvaluations` **pending** (1 missed rule-group evaluation in 5m); 6 `CPUThrottlingHigh` alerts pending or firing. Not a correctness bug — a sizing note for Part 5's one-command bootstrap, where a cold cluster starts everything at once |

### Requirement scorecard

| Requirement | Verdict |
|---|---|
| App-level metrics: request rate, error rate, latency, resource usage | **Met**, with the A-1 caveat: request rate, error rate and latency are all present and live; *process-level* resource usage is claimed by this document but absent. Container-level resource usage is covered properly by cAdvisor plus kube-state-metrics and is on the dashboard, and the § Runtime-vs-custom division-of-labor table is the right answer to "why not app-level" — it just over-claims one row |
| Monitoring stack deployed in-cluster | **Met.** 13/14 targets up; the one down target is A-2 |
| At least one dashboard answering "is this service healthy right now" | **Met** at the panel level, **weak** at the navigation level (A-8) |
| At least one meaningful alert, ready to fire, thresholds justified, one deliberate non-alert | **Met.** 3 alerts loaded and evaluating with `health: ok`; expression shapes proven capable of firing by substitution; thresholds justified above; `no_rule_matched` documented as the deliberate non-alert with a real argument (traffic-mix-shaped, not service-controlled) |

**Not re-verified this pass:** the prod cluster (`k3d-pokeproxy-prod` is not running right now — only the dev cluster is up), and no alert was forced through pending-to-firing again; step 6 already did that against real induced failures and this pass verified the expressions by structure and by substitution instead of re-inducing outages.

### Fixes — 2026-08-25, all live-verified against the dev cluster

**A-1 (blocker), fixed.** `Metrics.create()` now calls `disable_created_metrics()` (A-12, same commit) and registers `ProcessCollector(registry=registry)`, `PlatformCollector(registry=registry)`, `GCCollector(registry=registry)` on the per-instance registry. Redeployed, rolled the app pods (image tag unchanged — same git sha, working tree uncommitted — so a plain `helm upgrade` doesn't repull; `kubectl rollout restart deployment/pokeproxy` forced it). Live: `curl http://<pod>:8000/metrics` and `{job="pokeproxy", __name__=~"process_.*|python_.*"}` against Prometheus both return 32 series covering all five families (`process_cpu_seconds_total`, `process_resident_memory_bytes`, `process_open_fds`, `python_gc_objects_collected_total`, `python_info`, plus `process_max_fds`/`process_start_time_seconds`/`process_virtual_memory_bytes`/`python_gc_collections_total`/`python_gc_objects_uncollectable_total` that ship with the same collectors). The plan's "ships for free" claim is now true instead of corrected.

**A-2 (blocker), fixed.** Added `grafana.serviceMonitor.path: /grafana/metrics` to `deploy/monitoring/values.yaml` — the key exists and is documented (commented, default `/metrics`) in the installed chart's own `values.yaml` (`helm show values prometheus-community/kube-prometheus-stack --version 88.5.4`), confirmed before use rather than guessed. `helm template` diff before/after showed only the ServiceMonitor's `path:` field changing. Live: `/api/v1/targets` — `kube-prometheus-stack-grafana` `health: up`, `scrapeUrl: http://10.42.0.23:3000/grafana/metrics` (was `down`, `dial tcp [::1]:80: connect: connection refused`). `ALERTS{alertname="TargetDown",job=~".*grafana.*"}` now returns zero results (was `firing` continuously).

**A-3 (should-fix), fixed — chose option (a), drop the Prometheus Ingress.** `prometheus.ingress.enabled: false` in `deploy/monitoring/values.yaml`; also dropped `routePrefix`/`externalUrl` (`/prometheus`) since they existed only to make the Ingress path work — keeping them with no Ingress would leave Prometheus refusing to serve at `/` on a plain port-forward, a strictly worse state. **Reasoning for the team lead:** consistent with D4 (app's own `/metrics` kept off the Ingress for the identical reason: no auth layer exists for it) and with the just-closed `docs/issues/025` — a Traefik BasicAuth middleware (option b) was considered and rejected as more moving parts (a Middleware + Secret + IngressRoute for a *second*, separately-installed Helm release) to buy a laptop-only dev convenience that port-forward already covers with zero extra attack surface. `deploy/README.md`'s monitoring section updated to document port-forward as the only path (A-7, same commit). Live: `curl -o /dev/null -w '%{http_code}' http://localhost:8080/prometheus/api/v1/query?query=up` → `404` (was `200`); `kubectl -n monitoring get ingress` shows only the Grafana Ingress; port-forward to `:9090` still serves `/` and `/api/v1/query` (`200`) as before, unaffected.

**A-4 (should-fix), fixed — chose renaming `page`→`critical` over adding routing.** Both `page`-severity alerts (`PokeProxyHighServerErrorRate`, `PokeProxyTargetsDown`) relabeled `severity: critical` in `prometheusrule.yaml` — matches the chart's built-in inhibit vocabulary (`severity = critical` / `=~ warning|info`) with zero new Alertmanager config, versus hand-rolling a `page`-aware route/inhibit pair for a home-assignment-scale alert set. Live: `/api/v1/rules` shows `PokeProxyHighServerErrorRate` and `PokeProxyTargetsDown` both `severity: critical`, `PokeProxyCacheBackendErrors` unchanged at `warning`.

**A-5 (should-fix), fixed.** `Metrics.create()` takes `rule_names: Iterable[str] = ()`; `main.py`'s `lifespan()` passes `[rule.reason for rule in app.state.rules]` (rules are already loaded earlier in the same function, before `Metrics.create()` was previously called — no new load path needed). For every rule name, `.labels(rule=name)` is called on `downstream_retries_total` and `.labels(rule=name, result=r)` for `r` in `{success, timeout, error}` on `downstream_requests_total`, touching the series into existence at 0 without incrementing them. New tests: `test_default_process_and_python_collectors_are_registered`, `test_downstream_metrics_are_preinitialized_to_zero_per_rule` in `app/tests/test_metrics.py`. Live, both panel-10 queries against Prometheus: `sum(rate(pokeproxy_downstream_retries_total[5m])) by (rule)` and the `result=~"error|timeout"` variant of `downstream_requests_total` each return exactly 3 series (`strong fire pokemon`, `legendary pokemon`, `tanky pokemon`), all at `0` — matches the acceptance criterion exactly.

**A-6 (should-fix), fixed.** Panel 5 gets a third query (`refId: C`, `ready/desired` ratio) with a `byName` override giving it its own `percentunit`-formatted, red-below-1/green-at-1 threshold — same shape as panel 16 (Targets up) — while A/B (`ready`, `desired` raw counts) keep the original static-blue display. Live: `kube_deployment_status_replicas_ready{...} / kube_deployment_spec_replicas{...}` returns `1` against the healthy 2-of-2 cluster (renders green); reasoned through rather than forced by scaling down, since panel 16's identical threshold shape already proves the mechanism against this exact stack, and deliberately didn't scale `pokeproxy` down mid-task to avoid disrupting the reviewer's environment.

**A-7 (should-fix), fixed.** `deploy/README.md`'s monitoring section rewritten: Grafana is now documented via the Ingress (`http://localhost:8080/grafana/`, also the new home dashboard per A-8) as the primary path, with the port-forward caveat (`localhost:3000/` 301s to a portless URL; use `localhost:3000/grafana/` if port-forwarding) kept for when the Ingress isn't available. Prometheus documented as port-forward-only with the A-3 reasoning inline. Both URLs live-checked (see A-3, A-8 evidence).

**A-8 (nice-to-have), fixed.** `grafana.ini: [dashboards] default_home_dashboard_path: /tmp/dashboards/pokeproxy-overview.json` added to `deploy/monitoring/values.yaml`. Live: `kubectl exec` into the Grafana pod confirms the rendered `grafana.ini` carries the setting; `GET /grafana/api/dashboards/home` (authenticated) returns `{"redirectUri":"/grafana/d/default-home-dashboard/pokeproxy-overview"}` instead of the default search-page behavior.

**A-9 (nice-to-have), fixed.** New stat panel (id 17) "PokeProxy alerts firing" in row 1, gridPos `x:16,y:1,w:4,h:4` — row 1 rebalanced from five panels at width 5/5/5/5/4 to six at width 4 each to fit it. Query: `count(ALERTS{alertname=~"PokeProxy.*",alertstate="firing"}) or vector(0)`, green at 0 / red at ≥1; both sides safe to wrap in `or vector(0)` here since a genuinely empty result means zero alerts firing, not an outage (same reasoning as panel 16). Live: query returns `0` against the healthy cluster.

**A-10 (nice-to-have), fixed — added a dedicated panel rather than changing panel 7.** New full-width timeseries panel (id 18) "Server error rate by pod", `sum(rate(pokeproxy_requests_total{status=~"5.."}[5m])) by (pod)`, inserted between rows 2 and 3 (rows 3/4 shifted down by 8 grid units accordingly). Left panel 7 ("Request rate by outcome") untouched — it's called out as "the single highest-information panel" in this doc's own dashboard section and adding a `pod` split would double its series count and change its stacked-area semantics for no benefit the new panel doesn't already cover. Confirmed `pod` is a real label on `pokeproxy_requests_total` before committing to the query (ServiceMonitor's default target-discovery relabeling, not something added by hand): `pokeproxy_requests_total{...}` carries `pod="pokeproxy-<hash>-<suffix>"`. Live: `sum(rate(pokeproxy_requests_total[5m])) by (pod)` (unfiltered, to prove the mechanism against real traffic rather than the currently-empty 5xx set) returns one series per pod with real nonzero rates.

**A-11 (nice-to-have), fixed.** Static `namespace: {{ .Release.Namespace }}` / `service: pokeproxy` added to all three alerts' `labels` in `prometheusrule.yaml`. Live: `/api/v1/rules` shows all three rules carrying `namespace: pokeproxy, service: pokeproxy` alongside `severity` — Alertmanager's `group_by: [namespace]` and the inhibit rules' `equal: [namespace, alertname]` now have a real value to match on instead of empty string.

**A-12 (nice-to-have), fixed — chose `disable_created_metrics()` over the env var.** Called once in `Metrics.create()` (both exist in 0.26.0; the in-code call needs no deploy-manifest change and is exercised by the test suite same as everything else in that function). Live: `curl .../metrics | grep -c _created` → `0` (was 31 of 187 series, 17%).

**A-13 (informational), re-observed, no fix.** `kubectl top pods -n monitoring` right after this session's redeploy: Grafana resident at `384Mi` — exactly its configured limit (it had just reloaded the updated dashboard/config, a real but transient spike, not sustained). CPU all low at rest (`grafana 20m/300m limit`, `prometheus 11m/500m limit`) — no throttling *right now*, but `ALERTS{alertname=~"CPUThrottlingHigh|PrometheusMissingRuleEvaluations"}` still shows 6 `CPUThrottlingHigh` alerts `pending` for `grafana-sc-dashboard`/`grafana-sc-datasources`/`node-exporter`/`e2e` — same components and same magnitude as the original audit found, confirming this is a standing characteristic of running the whole stack on one laptop core budget, not a one-off. No resource limit changed: no single `kubectl top` snapshot here showed a limit being hit under sustained load, which is the bar this project holds itself to for resizing (see A-13's original text). Worth carrying into Part 5 as a bootstrap-ordering/sizing input, not a Part 4 code fix.

**Verification after all fixes:** `ruff check .` clean, `pytest -q` **113 passed** (2 new tests for A-1/A-5), `helm lint deploy/helm/pokeproxy -f deploy/envs/local/values.yaml --strict` clean, `helm template` of the monitoring chart against the edited values renders exactly one `Ingress` (Grafana's) and the `path: /grafana/metrics` ServiceMonitor field. Full redeploy via `scripts/deploy.sh` + `kubectl rollout restart deployment/pokeproxy` (needed because the git sha, and therefore the image tag, is unchanged while the working tree is uncommitted).
