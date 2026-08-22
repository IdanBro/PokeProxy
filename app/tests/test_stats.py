"""Outcome-accounting regression tests for H4 + H5.

H4 — request_count and error_count must move together, so error_rate can
never read 0.0 during a total outage; rejections and no-rule-matched must be
countable even though they have no downstream URL to key on.
H5 — per-request response-time samples must not be kept at all; percentiles
are Prometheus/Grafana's job (Part 4), not this process's memory.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from pokeproxy.main import app
from pokeproxy.proto import pokemon_pb2
from pokeproxy.stats import EndpointStats, StatsRegistry

# --- pure unit tests --------------------------------------------------------


def test_error_rate_reflects_a_total_outage() -> None:
    stats = EndpointStats()

    for _ in range(5):
        stats.record_request(is_error=True)

    assert stats.request_count == 5
    assert stats.error_count == 5
    assert stats.error_rate == 1.0


def test_error_rate_is_zero_only_when_nothing_failed() -> None:
    stats = EndpointStats()

    for _ in range(5):
        stats.record_request(is_error=False)

    assert stats.error_rate == 0.0


def test_mixed_outcomes_produce_a_real_error_rate() -> None:
    stats = EndpointStats()
    stats.record_request(is_error=True)
    stats.record_request(is_error=True)
    stats.record_request(is_error=False)

    assert stats.error_rate == 2 / 3


def test_total_response_time_accumulates_across_requests() -> None:
    stats = EndpointStats()

    stats.record_response_time(0.5)
    stats.record_response_time(1.5)

    assert stats.total_response_time == 2.0


def test_avg_response_time_divides_by_request_count() -> None:
    stats = EndpointStats()
    stats.record_request(is_error=False)
    stats.record_request(is_error=False)
    stats.record_response_time(1.0)
    stats.record_response_time(3.0)

    assert stats.avg_response_time == 2.0


def test_avg_response_time_of_empty_stats_is_zero() -> None:
    assert EndpointStats().avg_response_time == 0.0


def test_registry_records_outcomes_without_a_url() -> None:
    registry = StatsRegistry()

    registry.record_outcome("rejected_signature_missing")
    registry.record_outcome("rejected_signature_missing")
    registry.record_outcome("no_rule_matched")

    assert registry.outcomes == {"rejected_signature_missing": 2, "no_rule_matched": 1}


def test_to_dict_separates_endpoints_from_outcomes() -> None:
    registry = StatsRegistry()
    registry.get("http://downstream/pokemon").record_request(is_error=False)
    registry.record_outcome("no_rule_matched")

    result = registry.to_dict()

    assert set(result) == {"endpoints", "outcomes"}
    assert result["endpoints"]["http://downstream/pokemon"]["request_count"] == 1
    assert result["outcomes"] == {"no_rule_matched": 1}


# --- end-to-end regression --------------------------------------------------

DEV_SECRET_B64 = "dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg=="  # noqa: S105 — local dev key


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEPROXY_HMAC_KEY", DEV_SECRET_B64)
    monkeypatch.setenv("POKEPROXY_CONFIG", "config/rules.json")


@pytest.fixture
def no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _miss(redis: object, cache_key: str) -> None:
        return None

    async def _store(redis: object, cache_key: str, pokemon: object) -> None:
        return None

    monkeypatch.setattr("pokeproxy.proxy.get_cached_pokemon", _miss)
    monkeypatch.setattr("pokeproxy.proxy.cache_pokemon", _store)


def _sign(body: bytes) -> str:
    return hmac.new(
        base64.b64decode(DEV_SECRET_B64), body, hashlib.sha256
    ).hexdigest()


def _pikachu() -> bytes:
    """Deliberately matches no rule in config/rules.json."""
    pokemon = pokemon_pb2.Pokemon()
    fields = {
        "number": 25, "name": "Pikachu", "type_one": "Electric", "type_two": "",
        "total": 320, "hit_points": 35, "attack": 55, "defense": 40,
        "special_attack": 50, "special_defense": 50, "speed": 90,
        "generation": 1, "legendary": False,
    }
    for key, value in fields.items():
        setattr(pokemon, key, value)
    return pokemon.SerializeToString()


def _legendary_pokemon() -> bytes:
    pokemon = pokemon_pb2.Pokemon()
    fields = {
        "number": 150, "name": "Mewtwo", "type_one": "Psychic", "type_two": "",
        "total": 680, "hit_points": 106, "attack": 110, "defense": 90,
        "special_attack": 154, "special_defense": 90, "speed": 130,
        "generation": 1, "legendary": True,
    }
    for key, value in fields.items():
        setattr(pokemon, key, value)
    return pokemon.SerializeToString()


DownstreamHandler = Callable[[httpx.Request], Awaitable[httpx.Response]]


@contextmanager
def _client_with_downstream(handler: DownstreamHandler) -> Iterator[TestClient]:
    with TestClient(app) as client:
        client.app.state.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        yield client


def test_rejected_request_is_counted_by_outcome() -> None:
    with TestClient(app) as client:
        client.post("/stream", content=b"anything")
        outcomes = client.app.state.stats.outcomes

    assert outcomes["rejected_signature_missing"] == 1


def test_no_rule_matched_is_counted_by_outcome(no_cache: None) -> None:
    body = _pikachu()
    with TestClient(app) as client:
        client.post("/stream", content=body, headers={"X-Grd-Signature": _sign(body)})
        outcomes = client.app.state.stats.outcomes

    assert outcomes["no_rule_matched"] == 1


def test_error_rate_stays_accurate_through_a_total_outage(
    no_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORWARD_MAX_ATTEMPTS", "1")

    async def always_refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    body = _legendary_pokemon()
    with _client_with_downstream(always_refuse) as client:
        for _ in range(3):
            client.post(
                "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
            )
        endpoint_stats = next(iter(client.app.state.stats.endpoints.values()))

    assert endpoint_stats.request_count == 3
    assert endpoint_stats.error_count == 3
    assert endpoint_stats.error_rate == 1.0


def test_internal_error_is_counted_by_outcome(
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
        outcomes = client.app.state.stats.outcomes

    assert response.status_code == 500
    assert outcomes["internal_error"] == 1


def test_bytes_received_accumulates_across_requests(no_cache: None) -> None:
    async def accept(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    with _client_with_downstream(accept) as client:
        client.post("/stream", content=body, headers={"X-Grd-Signature": _sign(body)})
        client.post("/stream", content=body, headers={"X-Grd-Signature": _sign(body)})
        endpoint_stats = next(iter(client.app.state.stats.endpoints.values()))

    assert endpoint_stats.bytes_received == len(body) * 2
