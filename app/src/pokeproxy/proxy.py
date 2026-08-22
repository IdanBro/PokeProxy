from __future__ import annotations

import asyncio
import hashlib
import hmac
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from pokeproxy.cache import cache_pokemon, get_cached_pokemon, make_cache_key
from pokeproxy.config import PokemonJSON, Rule, decode_pokemon
from pokeproxy.rules import match_pokemon
from pokeproxy.stats import StatsRegistry

router = APIRouter()

MAX_BODY_SIZE = 1_048_576  # 1 MiB
REQUEST_ID_HEADER = "X-Request-ID"

RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.ConnectError)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    deadline_seconds: float


ALLOWED_FORWARD_HEADERS: frozenset[str] = frozenset()

HOP_BY_HOP_RESPONSE_HEADERS = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
})


def verify_signature(secret: bytes, body: bytes, signature: str) -> bool:
    expected = hmac.new(key=secret, msg=body, digestmod=hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _build_forward_headers(
    original_headers: dict[str, str], reason: str, request_id: str
) -> dict[str, str]:
    headers: dict[str, str] = {
        k: v
        for k, v in original_headers.items()
        if k.lower() in ALLOWED_FORWARD_HEADERS
    }
    headers["Content-Type"] = "application/json"
    headers["X-Grd-Reason"] = reason
    headers[REQUEST_ID_HEADER] = request_id
    return headers


def _forwardable_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_RESPONSE_HEADERS
    }


def _next_backoff_delay(previous_delay: float, cap: float = 30.0) -> float:
    return min(previous_delay * 2 * (0.5 + random.random()), cap)  # noqa: S311


async def _forward_with_retry(
    client: httpx.AsyncClient,
    policy: RetryPolicy,
    url: str,
    content: bytes,
    headers: dict[str, str],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> httpx.Response:
    deadline = clock() + policy.deadline_seconds
    delay = 0.1

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await client.post(url, content=content, headers=headers)
        except RETRYABLE_ERRORS:
            attempts_exhausted = attempt == policy.max_attempts
            deadline_exceeded = clock() >= deadline
            if attempts_exhausted or deadline_exceeded:
                raise
            await sleep(delay)
            delay = _next_backoff_delay(delay)

    raise AssertionError("unreachable: loop always returns or raises")


async def _forward_request(
    request: Request,
    rule: Rule,
    pokemon: PokemonJSON,
    original_headers: dict[str, str],
    stats: StatsRegistry,
) -> Response:
    json_bytes = pokemon.model_dump_json().encode()
    headers = _build_forward_headers(
        original_headers, rule.reason, request.state.request_id
    )

    endpoint_stats = stats.get(rule.url)
    start = time.monotonic()

    try:
        resp = await _forward_with_retry(
            request.app.state.http_client,
            request.app.state.retry_policy,
            rule.url,
            json_bytes,
            headers,
        )

        endpoint_stats.record_request(is_error=resp.status_code >= 400)

        request.state.outcome = "forwarded"
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=_forwardable_response_headers(resp.headers),
        )
    except httpx.TimeoutException:
        endpoint_stats.record_request(is_error=True)
        request.state.outcome = "downstream_timeout"
        return JSONResponse(
            content={"error": "downstream timeout"},
            status_code=504,
        )
    except httpx.HTTPError:
        endpoint_stats.record_request(is_error=True)
        request.state.outcome = "downstream_error"
        return JSONResponse(
            content={"error": "downstream error"},
            status_code=502,
        )
    finally:
        elapsed = time.monotonic() - start
        endpoint_stats.record_response_time(elapsed)
        endpoint_stats.bytes_sent += len(json_bytes)


def _outcome_response(
    request: Request,
    stats: StatsRegistry,
    outcome: str,
    content: dict[str, object],
    status_code: int,
) -> JSONResponse:
    request.state.outcome = outcome
    stats.record_outcome(outcome)
    return JSONResponse(content=content, status_code=status_code)


@router.post("/stream")
async def stream(request: Request) -> Response:
    stats: StatsRegistry = request.app.state.stats

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_SIZE:
        return _outcome_response(
            request, stats, "rejected_too_large", {"error": "payload too large"}, 413
        )

    body = await request.body()
    if len(body) > MAX_BODY_SIZE:
        return _outcome_response(
            request, stats, "rejected_too_large", {"error": "payload too large"}, 413
        )

    secret: bytes = request.app.state.hmac_key
    redis_client = request.app.state.redis

    signature = request.headers.get("X-Grd-Signature", "")
    if not signature:
        return _outcome_response(
            request,
            stats,
            "rejected_signature_missing",
            {"error": "invalid signature"},
            401,
        )
    if not verify_signature(secret, body, signature):
        return _outcome_response(
            request,
            stats,
            "rejected_signature_invalid",
            {"error": "invalid signature"},
            401,
        )

    body_hash = hashlib.sha256(body).hexdigest()
    cache_key = make_cache_key(body_hash)

    cached = await get_cached_pokemon(redis_client, cache_key)
    if cached is not None:
        pokemon = PokemonJSON(**cached)
    else:
        try:
            pokemon = decode_pokemon(body)
        except ValueError:
            return _outcome_response(
                request, stats, "rejected_protobuf", {"error": "invalid protobuf"}, 400
            )
        await cache_pokemon(redis_client, cache_key, pokemon)

    matched_rule = match_pokemon(pokemon, request.app.state.rules)

    if matched_rule is None:
        return _outcome_response(request, stats, "no_rule_matched", {}, 200)

    endpoint_stats = stats.get(matched_rule.url)
    endpoint_stats.bytes_received += len(body)

    original_headers: dict[str, str] = dict(request.headers)

    return await _forward_request(
        request, matched_rule, pokemon, original_headers, stats
    )
