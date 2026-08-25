from __future__ import annotations

import base64
import hashlib
import hmac
import os
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pokeproxy.main import app
from pokeproxy.proto import pokemon_pb2


def pytest_configure() -> None:
    os.chdir(Path(__file__).resolve().parent.parent)


DEV_SECRET_B64 = "dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg=="  # noqa: S105 — local dev key
DEV_SECRET = base64.b64decode(DEV_SECRET_B64)


def _sign(body: bytes) -> str:
    return hmac.new(DEV_SECRET, body, hashlib.sha256).hexdigest()


def _pokemon_bytes(**fields: object) -> bytes:
    pokemon = pokemon_pb2.Pokemon()
    for key, value in fields.items():
        setattr(pokemon, key, value)
    return pokemon.SerializeToString()


def _legendary_pokemon() -> bytes:
    return _pokemon_bytes(
        number=150, name="Mewtwo", type_one="Psychic", type_two="",
        total=680, hit_points=106, attack=110, defense=90,
        special_attack=154, special_defense=90, speed=130,
        generation=1, legendary=True,
    )


def _pikachu() -> bytes:
    """A payload that deliberately matches no rule in config/rules.json."""
    return _pokemon_bytes(
        number=25, name="Pikachu", type_one="Electric", type_two="",
        total=320, hit_points=35, attack=55, defense=40,
        special_attack=50, special_defense=50, speed=90,
        generation=1, legendary=False,
    )


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEPROXY_HMAC_KEY", DEV_SECRET_B64)
    monkeypatch.setenv("POKEPROXY_CONFIG", "config/rules.json")


@pytest.fixture
def no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the cache so tests don't depend on a real Redis connection."""

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
def _client_with_downstream(
    handler: DownstreamHandler, redis: object | None = None
) -> Iterator[TestClient]:
    with TestClient(app) as client:
        if redis is not None:
            client.app.state.redis = redis
        client.app.state.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        yield client
