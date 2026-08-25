"""Prometheus instrumentation regression tests (Part 4).

Every terminal `/stream` outcome must increment `pokeproxy_requests_total`
exactly once, from the single access-log middleware, so a call site that
forgets to record itself is structurally impossible — the bug the deleted
`StatsRegistry` actually had (`forwarded`/`downstream_timeout`/
`downstream_error` never called `record_outcome()`). `/health`, `/ready` and
`/metrics` itself must never be counted, matching probe-path exclusion from
the access log.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from pokeproxy.main import app
from pokeproxy.metrics import METRICS_CONTENT_TYPE

from .conftest import _client_with_downstream, _legendary_pokemon, _pikachu, _sign


def _sample(client: TestClient, name: str, **labels: str) -> float | None:
    return client.app.state.metrics.registry.get_sample_value(name, labels)


# --- /metrics endpoint -------------------------------------------------------


def test_metrics_endpoint_returns_prometheus_text() -> None:
    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == METRICS_CONTENT_TYPE
    assert b"pokeproxy_build_info" in response.content


def test_build_info_reports_the_configured_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POKEPROXY_REVISION", "abc1234")

    with TestClient(app) as client:
        text = client.get("/metrics").text

    assert 'revision="abc1234"' in text


def test_build_info_default_revision_is_unknown() -> None:
    with TestClient(app) as client:
        text = client.get("/metrics").text

    assert 'revision="unknown"' in text


def test_probe_paths_are_not_counted() -> None:
    with TestClient(app) as client:
        client.get("/health")
        client.get("/ready")
        client.get("/metrics")
        registry = client.app.state.metrics.registry

    # A recorded-but-forgotten probe path would show up as outcome="unknown".
    assert registry.get_sample_value("pokeproxy_requests_total", {"outcome": "unknown", "status": "200"}) is None


# --- /stream outcomes ---------------------------------------------------------


def test_rejected_signature_missing_is_counted() -> None:
    with TestClient(app) as client:
        client.post("/stream", content=b"anything")
        value = _sample(
            client, "pokeproxy_requests_total", outcome="rejected_signature_missing", status="401"
        )

    assert value == 1


def test_rejected_too_large_is_counted() -> None:
    oversized = b"x" * (1_048_576 + 1)
    with TestClient(app) as client:
        client.post(
            "/stream", content=oversized, headers={"X-Grd-Signature": _sign(oversized)}
        )
        value = _sample(
            client, "pokeproxy_requests_total", outcome="rejected_too_large", status="413"
        )

    assert value == 1


def test_no_rule_matched_is_counted(no_cache: None) -> None:
    body = _pikachu()
    with TestClient(app) as client:
        client.post("/stream", content=body, headers={"X-Grd-Signature": _sign(body)})
        value = _sample(
            client, "pokeproxy_requests_total", outcome="no_rule_matched", status="200"
        )

    assert value == 1


def test_forwarded_success_is_counted_on_both_series(no_cache: None) -> None:
    async def accept(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    with _client_with_downstream(accept) as client:
        client.post("/stream", content=body, headers={"X-Grd-Signature": _sign(body)})

        assert _sample(client, "pokeproxy_requests_total", outcome="forwarded", status="200") == 1
        assert (
            _sample(
                client,
                "pokeproxy_downstream_requests_total",
                rule="legendary pokemon",
                result="success",
            )
            == 1
        )
        assert (
            client.app.state.metrics.registry.get_sample_value(
                "pokeproxy_downstream_duration_seconds_count",
                {"rule": "legendary pokemon", "result": "success"},
            )
            == 1
        )


def test_downstream_timeout_is_counted(
    no_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORWARD_MAX_ATTEMPTS", "1")

    async def always_time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    body = _legendary_pokemon()
    with _client_with_downstream(always_time_out) as client:
        client.post("/stream", content=body, headers={"X-Grd-Signature": _sign(body)})

        assert _sample(client, "pokeproxy_requests_total", outcome="downstream_timeout", status="504") == 1
        assert (
            _sample(
                client,
                "pokeproxy_downstream_requests_total",
                rule="legendary pokemon",
                result="timeout",
            )
            == 1
        )


def test_downstream_error_is_counted(
    no_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORWARD_MAX_ATTEMPTS", "1")

    async def always_protocol_error(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("boom", request=request)

    body = _legendary_pokemon()
    with _client_with_downstream(always_protocol_error) as client:
        client.post("/stream", content=body, headers={"X-Grd-Signature": _sign(body)})

        assert _sample(client, "pokeproxy_requests_total", outcome="downstream_error", status="502") == 1
        assert (
            _sample(
                client,
                "pokeproxy_downstream_requests_total",
                rule="legendary pokemon",
                result="error",
            )
            == 1
        )


def test_non_2xx_downstream_response_is_counted_as_a_downstream_error(
    no_cache: None,
) -> None:
    async def respond_service_unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "downstream unavailable"})

    body = _legendary_pokemon()
    with _client_with_downstream(respond_service_unavailable) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )

        assert response.status_code == 503
        assert (
            _sample(client, "pokeproxy_requests_total", outcome="downstream_non_2xx", status="503")
            == 1
        )
        assert (
            _sample(
                client,
                "pokeproxy_downstream_requests_total",
                rule="legendary pokemon",
                result="error",
            )
            == 1
        )


def test_oversized_downstream_response_is_counted_as_a_downstream_error(
    no_cache: None,
) -> None:
    async def respond_with_oversized_body(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (1_048_576 + 1))

    body = _legendary_pokemon()
    with _client_with_downstream(respond_with_oversized_body) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )

        assert response.status_code == 502
        assert (
            _sample(
                client,
                "pokeproxy_requests_total",
                outcome="downstream_response_too_large",
                status="502",
            )
            == 1
        )
        assert (
            _sample(
                client,
                "pokeproxy_downstream_requests_total",
                rule="legendary pokemon",
                result="error",
            )
            == 1
        )


def test_retries_are_counted(no_cache: None) -> None:
    call_count = 0

    async def fail_once_then_succeed(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    with _client_with_downstream(fail_once_then_succeed) as client:
        client.post("/stream", content=body, headers={"X-Grd-Signature": _sign(body)})

        assert (
            _sample(client, "pokeproxy_downstream_retries_total", rule="legendary pokemon")
            == 1
        )


def test_internal_error_is_counted(
    no_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("pokeproxy.proxy.match_pokemon", explode)

    body = _pikachu()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )
        value = _sample(
            client, "pokeproxy_requests_total", outcome="internal_error", status="500"
        )

    assert response.status_code == 500
    assert value == 1


def test_request_duration_is_observed() -> None:
    with TestClient(app) as client:
        client.post("/stream", content=b"anything")
        count = client.app.state.metrics.registry.get_sample_value(
            "pokeproxy_request_duration_seconds_count"
        )

    assert count == 1


# --- Default collectors (A-1) -------------------------------------------------


def test_default_process_and_python_collectors_are_registered() -> None:
    """Metrics.create() must register these onto its own registry (A-1) --
    prometheus_client's default collectors otherwise live on the
    module-level registry and never appear on our per-instance one."""
    with TestClient(app) as client:
        text = client.get("/metrics").text

    for name in (
        "process_cpu_seconds_total",
        "process_resident_memory_bytes",
        "process_open_fds",
        "python_gc_objects_collected_total",
        "python_info",
    ):
        assert name in text, f"{name} missing from /metrics"


# --- Pre-initialized downstream label combinations (A-5) ---------------------


def test_downstream_metrics_are_preinitialized_to_zero_per_rule() -> None:
    """Every (rule, result) combination and every rule must exist at 0 from
    process start, or `sum(...) by (rule)` renders "No data" instead of a
    real 0 until the first real retry/error happens for that rule."""
    with TestClient(app) as client:
        for rule in ("strong fire pokemon", "legendary pokemon", "tanky pokemon"):
            assert _sample(client, "pokeproxy_downstream_retries_total", rule=rule) == 0
            for result in ("success", "timeout", "error"):
                assert (
                    _sample(
                        client,
                        "pokeproxy_downstream_requests_total",
                        rule=rule,
                        result=result,
                    )
                    == 0
                )
