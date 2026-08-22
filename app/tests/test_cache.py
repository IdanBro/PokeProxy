from __future__ import annotations

import json
import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from pokeproxy.cache import CACHE_TTL, cache_pokemon, get_cached_pokemon, make_cache_key
from pokeproxy.config import PokemonJSON


class UnavailableRedis:
    async def get(self, key: str) -> str | None:
        raise RedisConnectionError("connection refused")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise RedisConnectionError("connection refused")


class FakeRedis:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store = dict(store or {})
        self.get_calls = 0
        self.keys_calls = 0
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> str | None:
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_calls.append((key, value, ex))
        self.store[key] = value

    async def keys(self, pattern: str) -> list[str]:
        self.keys_calls += 1
        return list(self.store.keys())


def _pikachu() -> PokemonJSON:
    return PokemonJSON(
        number=25, name="Pikachu", type_one="Electric", type_two="",
        total=320, hit_points=35, attack=55, defense=40,
        special_attack=50, special_defense=50, speed=90,
        generation=1, legendary=False,
    )


async def test_returns_none_on_miss() -> None:
    redis = FakeRedis()

    result = await get_cached_pokemon(redis, "pokeproxy:pokemon:missing")

    assert result is None


async def test_returns_cached_value_on_hit() -> None:
    pokemon = _pikachu()
    key = make_cache_key("abc123")
    redis = FakeRedis({key: pokemon.model_dump_json()})

    result = await get_cached_pokemon(redis, key)

    assert result == json.loads(pokemon.model_dump_json())


async def test_lookup_never_scans_the_keyspace() -> None:
    key = make_cache_key("target")
    redis = FakeRedis({key: "{}"})

    await get_cached_pokemon(redis, key)

    assert redis.keys_calls == 0


async def test_lookup_cost_is_independent_of_keyspace_size() -> None:
    key = make_cache_key("target")
    unrelated = {make_cache_key(str(i)): "{}" for i in range(500)}
    redis = FakeRedis({**unrelated, key: '{"name": "Mewtwo"}'})

    result = await get_cached_pokemon(redis, key)

    assert result == {"name": "Mewtwo"}
    assert redis.get_calls == 1


async def test_cache_pokemon_stores_with_ttl() -> None:
    pokemon = _pikachu()
    key = make_cache_key("abc123")
    redis = FakeRedis()

    await cache_pokemon(redis, key, pokemon)

    assert redis.set_calls == [(key, pokemon.model_dump_json(), CACHE_TTL)]


def test_make_cache_key_is_namespaced() -> None:
    assert make_cache_key("abc123") == "pokeproxy:pokemon:abc123"


async def test_lookup_failure_is_treated_as_a_miss() -> None:
    result = await get_cached_pokemon(UnavailableRedis(), "pokeproxy:pokemon:x")

    assert result is None


async def test_lookup_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pokeproxy"):
        await get_cached_pokemon(UnavailableRedis(), "pokeproxy:pokemon:x")

    assert "cache lookup failed" in caplog.text


async def test_write_failure_does_not_raise() -> None:
    await cache_pokemon(UnavailableRedis(), make_cache_key("x"), _pikachu())


async def test_write_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pokeproxy"):
        await cache_pokemon(UnavailableRedis(), make_cache_key("x"), _pikachu())

    assert "cache write failed" in caplog.text
