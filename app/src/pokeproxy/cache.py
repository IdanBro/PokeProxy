from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING, Any

from redis.exceptions import RedisError

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from pokeproxy.metrics import Metrics

logger = logging.getLogger("pokeproxy")


async def get_cached_response(
    redis: aioredis.Redis, cache_key: str, metrics: Metrics
) -> dict[str, Any] | None:
    try:
        data = await redis.get(cache_key)
    except RedisError:
        logger.warning("cache lookup failed, treating as a miss", exc_info=True)
        metrics.cache_operations_total.labels(operation="get", result="error").inc()
        return None
    if data is None:
        metrics.cache_operations_total.labels(operation="get", result="miss").inc()
        return None
    try:
        stored = json.loads(data)
        response = {
            "status_code": stored["status_code"],
            "headers": stored["headers"],
            "content": base64.b64decode(stored["content_b64"]),
        }
    except (KeyError, TypeError, ValueError):
        logger.warning("cache entry malformed, treating as a miss", exc_info=True)
        metrics.cache_operations_total.labels(operation="get", result="error").inc()
        return None
    metrics.cache_operations_total.labels(operation="get", result="hit").inc()
    return response


async def cache_response(
    redis: aioredis.Redis,
    cache_key: str,
    status_code: int,
    headers: dict[str, str],
    content: bytes,
    ttl_seconds: int,
    metrics: Metrics,
) -> None:
    payload = json.dumps(
        {
            "status_code": status_code,
            "headers": headers,
            "content_b64": base64.b64encode(content).decode(),
        }
    )
    try:
        await redis.set(cache_key, payload, ex=ttl_seconds)
        metrics.cache_operations_total.labels(operation="set", result="success").inc()
    except RedisError:
        logger.warning("cache write failed, continuing without caching", exc_info=True)
        metrics.cache_operations_total.labels(operation="set", result="error").inc()


def make_cache_key(body_hash: str) -> str:
    return f"pokeproxy:pokemon:{body_hash}"
