"""Deduplication regression tests for M4.

A cache hit must replay the original downstream response byte-for-byte and
skip forwarding entirely — not just skip the protobuf decode. Only a genuine
2xx downstream response (`forwarded`) gets cached; anything else —
a proxy-side failure (`downstream_timeout`/`downstream_error`), a non-2xx
downstream response (`downstream_non_2xx`), or an oversized downstream body
(`downstream_response_too_large`) — must not be cached, so a retried
duplicate gets a fresh attempt once downstream recovers.
"""

from __future__ import annotations

import httpx
import pytest

from pokeproxy.proto import pokemon_pb2

from .conftest import _client_with_downstream, _legendary_pokemon, _sign


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


def test_duplicate_payload_replays_the_cached_response_without_forwarding_again() -> None:
    call_count = 0

    async def accept(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    headers = {"X-Grd-Signature": _sign(body)}
    with _client_with_downstream(accept, redis=FakeRedis()) as client:
        first = client.post("/stream", content=body, headers=headers)
        second = client.post("/stream", content=body, headers=headers)

    assert call_count == 1
    assert first.status_code == second.status_code == 200
    assert first.content == second.content


def test_duplicate_response_is_counted_without_inflating_downstream_requests() -> None:
    async def accept(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    headers = {"X-Grd-Signature": _sign(body)}
    with _client_with_downstream(accept, redis=FakeRedis()) as client:
        client.post("/stream", content=body, headers=headers)
        client.post("/stream", content=body, headers=headers)
        registry = client.app.state.metrics.registry

    assert (
        registry.get_sample_value(
            "pokeproxy_requests_total",
            {"outcome": "duplicate_suppressed", "status": "200"},
        )
        == 1
    )
    assert (
        registry.get_sample_value(
            "pokeproxy_downstream_requests_total",
            {"rule": "legendary pokemon", "result": "success"},
        )
        == 1
    )


def test_downstream_failure_is_not_cached_and_the_next_duplicate_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORWARD_MAX_ATTEMPTS", "1")
    call_count = 0

    async def fail_once_then_succeed(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    headers = {"X-Grd-Signature": _sign(body)}
    with _client_with_downstream(fail_once_then_succeed, redis=FakeRedis()) as client:
        first = client.post("/stream", content=body, headers=headers)
        second = client.post("/stream", content=body, headers=headers)

    assert first.status_code == 502
    assert second.status_code == 200
    assert call_count == 2


def test_non_2xx_downstream_response_is_not_cached_and_the_next_duplicate_retries() -> None:
    call_count = 0

    async def fail_once_then_succeed(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(503, json={"error": "downstream unavailable"})
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    headers = {"X-Grd-Signature": _sign(body)}
    with _client_with_downstream(fail_once_then_succeed, redis=FakeRedis()) as client:
        first = client.post("/stream", content=body, headers=headers)
        second = client.post("/stream", content=body, headers=headers)

    assert first.status_code == 503
    assert second.status_code == 200
    assert call_count == 2


def test_oversized_downstream_response_is_not_cached_and_the_next_duplicate_retries() -> None:
    call_count = 0

    async def fail_once_then_succeed(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, content=b"x" * (1_048_576 + 1))
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    headers = {"X-Grd-Signature": _sign(body)}
    with _client_with_downstream(fail_once_then_succeed, redis=FakeRedis()) as client:
        first = client.post("/stream", content=body, headers=headers)
        second = client.post("/stream", content=body, headers=headers)

    assert first.status_code == 502
    assert second.status_code == 200
    assert call_count == 2


def test_different_payloads_are_not_deduplicated() -> None:
    call_count = 0

    async def accept(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"status": "received"})

    with _client_with_downstream(accept, redis=FakeRedis()) as client:
        for number, name in ((150, "Mewtwo"), (144, "Articuno")):
            pokemon = pokemon_pb2.Pokemon()
            fields = {
                "number": number, "name": name, "type_one": "Psychic", "type_two": "",
                "total": 680, "hit_points": 106, "attack": 110, "defense": 90,
                "special_attack": 154, "special_defense": 90, "speed": 130,
                "generation": 1, "legendary": True,
            }
            for key, value in fields.items():
                setattr(pokemon, key, value)
            body = pokemon.SerializeToString()
            client.post("/stream", content=body, headers={"X-Grd-Signature": _sign(body)})

    assert call_count == 2
