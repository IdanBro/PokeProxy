from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING, Any

from redis.exceptions import RedisError

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger("pokeproxy")


async def get_cached_response(redis: aioredis.Redis, cache_key: str) -> dict[str, Any] | None:
    try:
        data = await redis.get(cache_key)
    except RedisError:
        logger.warning("cache lookup failed, treating as a miss", exc_info=True)
        return None
    if data is None:
        return None
    stored = json.loads(data)
    return {
        "status_code": stored["status_code"],
        "headers": stored["headers"],
        "content": base64.b64decode(stored["content_b64"]),
    }


async def cache_response(
    redis: aioredis.Redis,
    cache_key: str,
    status_code: int,
    headers: dict[str, str],
    content: bytes,
    ttl_seconds: float,
) -> None:
    payload = json.dumps(
        {
            "status_code": status_code,
            "headers": headers,
            "content_b64": base64.b64encode(content).decode(),
        }
    )
    try:
        await redis.set(cache_key, payload, ex=int(ttl_seconds))
    except RedisError:
        logger.warning("cache write failed, continuing without caching", exc_info=True)


def make_cache_key(body_hash: str) -> str:
    return f"pokeproxy:pokemon:{body_hash}"
