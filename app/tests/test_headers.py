"""Header-hygiene regression tests for H2 + H3.

H3 — only an explicit allowlist of client headers may reach downstream; the
proxy builds every header downstream actually needs itself.
H2 — hop-by-hop headers on the downstream response (framing, connection
management, encoding) must not be relayed to the client verbatim.
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
from pokeproxy.proxy import _build_forward_headers, _forwardable_response_headers

DEV_SECRET_B64 = "dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg=="  # noqa: S105 — local dev key


def test_no_client_header_reaches_downstream_by_default() -> None:
    original = {
        "authorization": "Bearer secret",
        "cookie": "session=abc",
        "x-custom-client-header": "value",
        "connection": "keep-alive",
    }

    headers = _build_forward_headers(original, "reason", "req-1")

    assert set(headers) == {"Content-Type", "X-Grd-Reason", "X-Request-ID"}


def test_forward_headers_are_always_set_regardless_of_client_input() -> None:
    headers = _build_forward_headers({}, "strong fire pokemon", "req-2")

    assert headers["Content-Type"] == "application/json"
    assert headers["X-Grd-Reason"] == "strong fire pokemon"
    assert headers["X-Request-ID"] == "req-2"


def test_hop_by_hop_response_headers_are_stripped() -> None:
    headers = httpx.Headers(
        [
            ("Content-Type", "application/json"),
            ("Content-Length", "123"),
            ("Content-Encoding", "gzip"),
            ("Transfer-Encoding", "chunked"),
            ("Connection", "keep-alive"),
            ("Keep-Alive", "timeout=5"),
        ]
    )

    result = _forwardable_response_headers(headers)

    assert result == {"content-type": "application/json"}


def test_non_hop_by_hop_response_headers_pass_through() -> None:
    headers = httpx.Headers(
        [("Content-Type", "application/json"), ("X-Downstream-Trace", "abc123")]
    )

    result = _forwardable_response_headers(headers)

    assert result["x-downstream-trace"] == "abc123"


DEV_SECRET = base64.b64decode(DEV_SECRET_B64)


def _sign(body: bytes) -> str:
    return hmac.new(DEV_SECRET, body, hashlib.sha256).hexdigest()


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


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEPROXY_HMAC_KEY", DEV_SECRET_B64)
    monkeypatch.setenv("POKEPROXY_CONFIG", "config/rules.json")


@pytest.fixture
def no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _miss(redis: object, cache_key: str, metrics: object) -> None:
        return None

    async def _store(
        redis: object,
        cache_key: str,
        status_code: int,
        headers: dict[str, str],
        content: bytes,
        ttl_seconds: float,
        metrics: object,
    ) -> None:
        return None

    monkeypatch.setattr("pokeproxy.proxy.get_cached_response", _miss)
    monkeypatch.setattr("pokeproxy.proxy.cache_response", _store)


DownstreamHandler = Callable[[httpx.Request], Awaitable[httpx.Response]]


@contextmanager
def _client_with_downstream(handler: DownstreamHandler) -> Iterator[TestClient]:
    with TestClient(app) as client:
        client.app.state.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        yield client


def test_client_headers_are_not_relayed_downstream_end_to_end(no_cache: None) -> None:
    received: dict[str, str] = {}

    async def capture(request: httpx.Request) -> httpx.Response:
        received.update(request.headers)
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    with _client_with_downstream(capture) as client:
        client.post(
            "/stream",
            content=body,
            headers={
                "X-Grd-Signature": _sign(body),
                "Authorization": "Bearer should-not-leak",
                "Cookie": "session=should-not-leak",
            },
        )

    assert "authorization" not in received
    assert "cookie" not in received
    assert received["x-grd-reason"] == "legendary pokemon"


def test_downstream_hop_by_hop_headers_are_not_relayed_to_client(
    no_cache: None,
) -> None:
    async def respond_with_framing_headers(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "received"},
            headers={"Connection": "keep-alive", "X-Downstream-Trace": "abc123"},
        )

    body = _legendary_pokemon()
    with _client_with_downstream(respond_with_framing_headers) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )

    assert "connection" not in response.headers
    assert response.headers["x-downstream-trace"] == "abc123"
