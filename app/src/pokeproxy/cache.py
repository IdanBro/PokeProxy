from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from pokeproxy.config import PokemonJSON

CACHE_TTL = 300  # 5 minutes

logger = logging.getLogger("pokeproxy")


async def get_cached_pokemon(redis: aioredis.Redis, cache_key: str) -> dict | None:
    try:
        data = await redis.get(cache_key)
    except RedisError:
        logger.warning("cache lookup failed, treating as a miss", exc_info=True)
        return None
    if data is None:
        return None
    return json.loads(data)


async def cache_pokemon(
    redis: aioredis.Redis, cache_key: str, pokemon: PokemonJSON
) -> None:
    try:
        await redis.set(cache_key, pokemon.model_dump_json(), ex=CACHE_TTL)
    except RedisError:
        logger.warning("cache write failed, continuing without caching", exc_info=True)


def make_cache_key(body_hash: str) -> str:
    return f"pokeproxy:pokemon:{body_hash}"
