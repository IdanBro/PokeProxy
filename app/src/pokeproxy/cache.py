from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from pokeproxy.config import PokemonJSON

CACHE_TTL = 300  # 5 minutes


async def get_cached_pokemon(redis: aioredis.Redis, cache_key: str) -> dict | None:
    data = await redis.get(cache_key)
    if data is None:
        return None
    return json.loads(data)


async def cache_pokemon(
    redis: aioredis.Redis, cache_key: str, pokemon: PokemonJSON
) -> None:
    await redis.set(cache_key, pokemon.model_dump_json(), ex=CACHE_TTL)


def make_cache_key(body_hash: str) -> str:
    return f"pokeproxy:pokemon:{body_hash}"
