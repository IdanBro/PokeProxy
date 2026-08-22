from __future__ import annotations

import json
import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from pokeproxy.cache import cache_response, get_cached_response, make_cache_key


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


async def test_returns_none_on_miss() -> None:
    redis = FakeRedis()

    result = await get_cached_response(redis, "pokeproxy:pokemon:missing")

    assert result is None


async def test_returns_cached_response_on_hit() -> None:
    key = make_cache_key("abc123")
    redis = FakeRedis()
    await cache_response(redis, key, 200, {"content-type": "application/json"}, b'{"status": "received"}', 300.0)

    result = await get_cached_response(redis, key)

    assert result == {
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "content": b'{"status": "received"}',
    }


async def test_round_trips_arbitrary_binary_content() -> None:
    key = make_cache_key("bin")
    redis = FakeRedis()
    binary_content = bytes(range(256))
    await cache_response(redis, key, 200, {}, binary_content, 300.0)

    result = await get_cached_response(redis, key)

    assert result is not None
    assert result["content"] == binary_content


async def test_lookup_never_scans_the_keyspace() -> None:
    key = make_cache_key("target")
    redis = FakeRedis()
    await cache_response(redis, key, 200, {}, b"{}", 300.0)

    await get_cached_response(redis, key)

    assert redis.keys_calls == 0


async def test_lookup_cost_is_independent_of_keyspace_size() -> None:
    key = make_cache_key("target")
    redis = FakeRedis({make_cache_key(str(i)): "{}" for i in range(500)})
    await cache_response(redis, key, 200, {}, b'{"name": "Mewtwo"}', 300.0)

    result = await get_cached_response(redis, key)

    assert result is not None
    assert result["content"] == b'{"name": "Mewtwo"}'
    assert redis.get_calls == 1


async def test_cache_response_stores_with_the_configured_ttl() -> None:
    key = make_cache_key("abc123")
    redis = FakeRedis()

    await cache_response(redis, key, 200, {}, b"{}", 42.0)

    assert len(redis.set_calls) == 1
    stored_key, stored_value, ex = redis.set_calls[0]
    assert stored_key == key
    assert ex == 42
    assert json.loads(stored_value)["status_code"] == 200


def test_make_cache_key_is_namespaced() -> None:
    assert make_cache_key("abc123") == "pokeproxy:pokemon:abc123"


async def test_lookup_failure_is_treated_as_a_miss() -> None:
    result = await get_cached_response(UnavailableRedis(), "pokeproxy:pokemon:x")

    assert result is None


async def test_lookup_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pokeproxy"):
        await get_cached_response(UnavailableRedis(), "pokeproxy:pokemon:x")

    assert "cache lookup failed" in caplog.text


async def test_write_failure_does_not_raise() -> None:
    await cache_response(UnavailableRedis(), make_cache_key("x"), 200, {}, b"{}", 300.0)


async def test_write_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pokeproxy"):
        await cache_response(UnavailableRedis(), make_cache_key("x"), 200, {}, b"{}", 300.0)

    assert "cache write failed" in caplog.text
