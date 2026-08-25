from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from pokeproxy.config import Rule, Settings
from pokeproxy.logging_config import setup_logging
from pokeproxy.metrics import METRICS_CONTENT_TYPE, Metrics
from pokeproxy.proxy import REQUEST_ID_HEADER, RetryPolicy
from pokeproxy.proxy import router as proxy_router
from pokeproxy.rules import load_rules

# Configure logging at import time. Uvicorn sets up its own logging before it
# imports the app, so doing it here means even its startup lines come out as
# JSON instead of the first few lines being plaintext.
setup_logging()

logger = logging.getLogger("pokeproxy")

# kubelet and Prometheus poll these continuously; counting or logging them
# would bury real traffic under probe noise in both the access log and the
# request-rate/latency metrics.
_UNINSTRUMENTED_PATHS = frozenset({"/health", "/ready", "/metrics"})


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


def _build_http_client(settings: Settings) -> httpx.AsyncClient:
    """Build the shared downstream client.

    `read`/`write` are bounded by `forward_attempt_timeout_seconds`, not
    `forward_deadline_seconds` — the deadline is a budget across every retry
    attempt combined, so a per-attempt timeout equal to it lets one slow
    attempt swallow the whole budget and leaves `forward_max_attempts` unable
    to retry. `Settings` enforces attempt_timeout < deadline at startup.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=5.0,
            read=settings.forward_attempt_timeout_seconds,
            write=settings.forward_attempt_timeout_seconds,
            pool=5.0,
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = _load_settings()
    logging.getLogger().setLevel(settings.log_level.upper())

    app.state.rules = _load_rules(settings.pokeproxy_config)
    app.state.hmac_key = settings.hmac_key
    app.state.metrics = Metrics.create(
        revision=settings.pokeproxy_revision,
        version=settings.build_version,
        rule_names=[rule.reason for rule in app.state.rules],
    )

    http_client = _build_http_client(settings)
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
    instrumented = request.url.path not in _UNINSTRUMENTED_PATHS
    metrics: Metrics = request.app.state.metrics

    def elapsed_seconds() -> float:
        return time.perf_counter() - start

    def record(outcome: str, status: int) -> None:
        if not instrumented:
            return
        metrics.requests_total.labels(outcome=outcome, status=str(status)).inc()
        metrics.request_duration_seconds.observe(elapsed_seconds())

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
                "duration_ms": round(elapsed_seconds() * 1000, 2),
                "outcome": "internal_error",
            },
        )
        record("internal_error", 500)
        return JSONResponse(
            content={"error": "internal error", "request_id": request_id},
            status_code=500,
            headers={REQUEST_ID_HEADER: request_id},
        )

    response.headers[REQUEST_ID_HEADER] = request_id

    # "unknown" rather than "ok" so a terminal path I forgot to label shows
    # up as a gap instead of hiding as a success.
    outcome = request.state.outcome or "unknown"
    record(outcome, response.status_code)

    if instrumented:
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "outcome": outcome,
                "duration_ms": round(elapsed_seconds() * 1000, 2),
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


@app.get("/metrics")
async def metrics(request: Request) -> Response:
    app_metrics: Metrics = request.app.state.metrics
    return Response(content=app_metrics.render(), media_type=METRICS_CONTENT_TYPE)
