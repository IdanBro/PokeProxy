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

from pokeproxy.cache import cache_response, get_cached_response, make_cache_key
from pokeproxy.config import PokemonJSON, Rule, decode_pokemon
from pokeproxy.metrics import Metrics
from pokeproxy.rules import match_pokemon

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
    metrics: Metrics,
    rule_label: str,
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
            metrics.downstream_retries_total.labels(rule=rule_label).inc()
            await sleep(delay)
            delay = _next_backoff_delay(delay)

    raise AssertionError("unreachable: loop always returns or raises")


async def _forward_request(
    request: Request,
    rule: Rule,
    pokemon: PokemonJSON,
    original_headers: dict[str, str],
    metrics: Metrics,
    cache_key: str,
) -> Response:
    json_bytes = pokemon.model_dump_json().encode()
    headers = _build_forward_headers(
        original_headers, rule.reason, request.state.request_id
    )

    start = time.monotonic()

    try:
        resp = await _forward_with_retry(
            request.app.state.http_client,
            request.app.state.retry_policy,
            rule.url,
            json_bytes,
            headers,
            metrics,
            rule.reason,
        )

        metrics.downstream_requests_total.labels(
            rule=rule.reason, result="success"
        ).inc()

        request.state.outcome = "forwarded"
        response_headers = _forwardable_response_headers(resp.headers)
        await cache_response(
            request.app.state.redis,
            cache_key,
            resp.status_code,
            response_headers,
            resp.content,
            request.app.state.cache_ttl_seconds,
            metrics,
        )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=response_headers,
        )
    except httpx.TimeoutException:
        metrics.downstream_requests_total.labels(
            rule=rule.reason, result="timeout"
        ).inc()
        request.state.outcome = "downstream_timeout"
        return JSONResponse(
            content={"error": "downstream timeout"},
            status_code=504,
        )
    except httpx.HTTPError:
        metrics.downstream_requests_total.labels(
            rule=rule.reason, result="error"
        ).inc()
        request.state.outcome = "downstream_error"
        return JSONResponse(
            content={"error": "downstream error"},
            status_code=502,
        )
    finally:
        elapsed = time.monotonic() - start
        metrics.downstream_duration_seconds.labels(rule=rule.reason).observe(elapsed)


def _outcome_response(
    request: Request,
    outcome: str,
    content: dict[str, object],
    status_code: int,
) -> JSONResponse:
    request.state.outcome = outcome
    return JSONResponse(content=content, status_code=status_code)


def _duplicate_response(request: Request, cached: dict[str, object]) -> Response:
    request.state.outcome = "duplicate_suppressed"
    return Response(
        content=cached["content"],
        status_code=cached["status_code"],
        headers=cached["headers"],
    )


@router.post("/stream")
async def stream(request: Request) -> Response:
    metrics: Metrics = request.app.state.metrics

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_SIZE:
        return _outcome_response(
            request, "rejected_too_large", {"error": "payload too large"}, 413
        )

    body = await request.body()
    if len(body) > MAX_BODY_SIZE:
        return _outcome_response(
            request, "rejected_too_large", {"error": "payload too large"}, 413
        )

    secret: bytes = request.app.state.hmac_key
    redis_client = request.app.state.redis

    signature = request.headers.get("X-Grd-Signature", "")
    if not signature:
        return _outcome_response(
            request, "rejected_signature_missing", {"error": "invalid signature"}, 401
        )
    if not verify_signature(secret, body, signature):
        return _outcome_response(
            request, "rejected_signature_invalid", {"error": "invalid signature"}, 401
        )

    body_hash = hashlib.sha256(body).hexdigest()
    cache_key = make_cache_key(body_hash)

    cached = await get_cached_response(redis_client, cache_key, metrics)
    if cached is not None:
        return _duplicate_response(request, cached)

    try:
        pokemon = decode_pokemon(body)
    except ValueError:
        return _outcome_response(
            request, "rejected_protobuf", {"error": "invalid protobuf"}, 400
        )

    matched_rule = match_pokemon(pokemon, request.app.state.rules)

    if matched_rule is None:
        return _outcome_response(request, "no_rule_matched", {}, 200)

    original_headers: dict[str, str] = dict(request.headers)

    return await _forward_request(
        request, matched_rule, pokemon, original_headers, metrics, cache_key
    )
