"""Prometheus instrumentation.

One `Metrics` instance lives on `app.state`, created fresh per app lifespan
(mirrors the old `StatsRegistry` pattern) rather than as module-level
globals — the app is single-process per pod (see `__main__.py`), so this
costs nothing in production and means each test's `TestClient(app)` gets an
isolated registry instead of accumulating counts across the whole test run.

Every terminal `/stream` outcome increments `requests_total` from exactly
one place — the access-log middleware in `main.py`, reading
`request.state.outcome` — so a call site that forgets to record itself is
structurally impossible, unlike the `StatsRegistry` it replaces.
"""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Straddles the sub-millisecond reject path (bad signature, oversized body)
# and forward_deadline_seconds (10s) so both ends of the request lifecycle
# land inside a bucket rather than all piling into +Inf or bucket 0.
_DURATION_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


@dataclass
class Metrics:
    registry: CollectorRegistry
    requests_total: Counter
    request_duration_seconds: Histogram
    downstream_requests_total: Counter
    downstream_duration_seconds: Histogram
    downstream_retries_total: Counter
    cache_operations_total: Counter

    @classmethod
    def create(cls, *, revision: str, version: str) -> Metrics:
        registry = CollectorRegistry()

        Gauge(
            "pokeproxy_build_info",
            "Always 1; labels identify the running build.",
            ["revision", "version"],
            registry=registry,
        ).labels(revision=revision, version=version).set(1)

        return cls(
            registry=registry,
            requests_total=Counter(
                "pokeproxy_requests_total",
                "Requests to /stream by terminal outcome and response status.",
                ["outcome", "status"],
                registry=registry,
            ),
            request_duration_seconds=Histogram(
                "pokeproxy_request_duration_seconds",
                "End-to-end /stream request duration as seen by the caller.",
                buckets=_DURATION_BUCKETS,
                registry=registry,
            ),
            downstream_requests_total=Counter(
                "pokeproxy_downstream_requests_total",
                "Forward attempts to a downstream rule endpoint, by rule and result.",
                ["rule", "result"],
                registry=registry,
            ),
            downstream_duration_seconds=Histogram(
                "pokeproxy_downstream_duration_seconds",
                "Downstream forward duration by rule.",
                ["rule"],
                buckets=_DURATION_BUCKETS,
                registry=registry,
            ),
            downstream_retries_total=Counter(
                "pokeproxy_downstream_retries_total",
                "Retry attempts against a downstream rule endpoint.",
                ["rule"],
                registry=registry,
            ),
            cache_operations_total=Counter(
                "pokeproxy_cache_operations_total",
                "Redis cache operations by operation and result.",
                ["operation", "result"],
                registry=registry,
            ),
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)


METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST
