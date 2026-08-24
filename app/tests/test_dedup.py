"""Deduplication regression tests for M4.

A cache hit must replay the original downstream response byte-for-byte and
skip forwarding entirely — not just skip the protobuf decode. Only a real
downstream response (`forwarded`) gets cached; a proxy-side failure
(`downstream_timeout`/`downstream_error`) must not be cached, so a retried
duplicate gets a fresh attempt once downstream recovers.
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

DEV_SECRET_B64 = "dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg=="  # noqa: S105 — local dev key


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEPROXY_HMAC_KEY", DEV_SECRET_B64)
    monkeypatch.setenv("POKEPROXY_CONFIG", "config/rules.json")


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


def _sign(body: bytes) -> str:
    return hmac.new(base64.b64decode(DEV_SECRET_B64), body, hashlib.sha256).hexdigest()


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
        client.app.state.redis = FakeRedis()
        client.app.state.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        yield client


def test_duplicate_payload_replays_the_cached_response_without_forwarding_again() -> None:
    call_count = 0

    async def accept(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    headers = {"X-Grd-Signature": _sign(body)}
    with _client_with_downstream(accept) as client:
        first = client.post("/stream", content=body, headers=headers)
        second = client.post("/stream", content=body, headers=headers)

    assert call_count == 1
    assert first.status_code == second.status_code == 200
    assert first.content == second.content


def test_duplicate_response_is_counted_by_outcome() -> None:
    async def accept(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    headers = {"X-Grd-Signature": _sign(body)}
    with _client_with_downstream(accept) as client:
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


def test_duplicate_replay_does_not_inflate_downstream_requests() -> None:
    async def accept(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    headers = {"X-Grd-Signature": _sign(body)}
    with _client_with_downstream(accept) as client:
        client.post("/stream", content=body, headers=headers)
        client.post("/stream", content=body, headers=headers)
        registry = client.app.state.metrics.registry

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
    with _client_with_downstream(fail_once_then_succeed) as client:
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

    with _client_with_downstream(accept) as client:
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
