from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from pokeproxy.config import Rule, Settings
from pokeproxy.logging_config import setup_logging
from pokeproxy.proxy import REQUEST_ID_HEADER, RetryPolicy
from pokeproxy.proxy import router as proxy_router
from pokeproxy.rules import load_rules
from pokeproxy.stats import StatsRegistry

# Configure logging at import time. Uvicorn sets up its own logging before it
# imports the app, so doing it here means even its startup lines come out as
# JSON instead of the first few lines being plaintext.
setup_logging()

logger = logging.getLogger("pokeproxy")

# kubelet polls these continuously; access lines for them would bury real traffic.
_UNLOGGED_PATHS = frozenset({"/health", "/ready", "/stats"})


def _load_settings() -> Settings:
    """Load configuration, failing with one clear line instead of a traceback."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in e.errors()
        )
        logger.critical(
            "configuration invalid, refusing to start", extra={"problems": problems}
        )
        raise SystemExit(1) from None


def _load_rules(config_path: str) -> list[Rule]:
    try:
        return load_rules(config_path)
    except (OSError, ValueError) as e:
        logger.critical(
            "rules configuration invalid, refusing to start",
            extra={"config_path": config_path, "error": str(e)},
        )
        raise SystemExit(1) from None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = _load_settings()
    logging.getLogger().setLevel(settings.log_level.upper())

    app.state.rules = _load_rules(settings.pokeproxy_config)
    app.state.hmac_key = settings.hmac_key
    app.state.stats = StatsRegistry()

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
    )
    app.state.http_client = http_client
    app.state.retry_policy = RetryPolicy(
        max_attempts=settings.forward_max_attempts,
        deadline_seconds=settings.forward_deadline_seconds,
    )

    redis_client = aioredis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )
    app.state.redis = redis_client
    app.state.cache_ttl_seconds = settings.cache_ttl_seconds

    app.state.ready = True
    logger.info(
        "startup complete",
        extra={
            "config_path": settings.pokeproxy_config,
            "log_level": settings.log_level,
        },
    )

    yield

    app.state.ready = False
    logger.info("shutdown started")
    await redis_client.aclose()
    await http_client.aclose()
    logger.info("shutdown complete")


app = FastAPI(title="PokeProxy", lifespan=lifespan)


@app.middleware("http")
async def access_log(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Assign a correlation ID and emit exactly one access line per request.

    Handlers record *why* a request ended the way it did via
    `request.state.outcome`; this is the single place that reads it.
    """
    request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
    request.state.request_id = request_id
    request.state.outcome = None

    start = time.perf_counter()

    def elapsed_ms() -> float:
        return round((time.perf_counter() - start) * 1000, 2)

    try:
        response = await call_next(request)
    except Exception:
        # Handle it here so the client gets a correlatable JSON error and the
        # failure is one structured record rather than a raw ASGI traceback.
        logger.exception(
            "request failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": 500,
                "duration_ms": elapsed_ms(),
                "outcome": "internal_error",
            },
        )
        request.app.state.stats.record_outcome("internal_error")
        return JSONResponse(
            content={"error": "internal error", "request_id": request_id},
            status_code=500,
            headers={REQUEST_ID_HEADER: request_id},
        )

    response.headers[REQUEST_ID_HEADER] = request_id

    if request.url.path not in _UNLOGGED_PATHS:
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                # "unknown" rather than "ok" so a terminal path I forgot to
                # label shows up as a gap instead of hiding as a success.
                "outcome": request.state.outcome or "unknown",
                "duration_ms": elapsed_ms(),
            },
        )

    return response


app.include_router(proxy_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/ready")
async def ready(request: Request) -> JSONResponse:
    if request.app.state.ready:
        return JSONResponse(content={"status": "ready"})
    return JSONResponse(content={"status": "not ready"}, status_code=503)


@app.get("/stats")
async def stats(request: Request) -> dict[str, Any]:
    stats_registry: StatsRegistry = request.app.state.stats
    return stats_registry.to_dict()
