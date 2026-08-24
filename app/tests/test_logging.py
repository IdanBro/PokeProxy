"""Structured logging and request-correlation tests.

Regression cover for C5: every request must produce exactly one JSON access
line carrying a correlation ID and an `outcome` saying why the request ended
the way it did. Before this, a bad signature and a *missing* signature logged
identically, and "no rule matched" returned 200 and logged nothing at all.

The `no_cache` fixture stubs out the cache so tests don't depend on a real
Redis connection. `_client_with_downstream` swaps in a mocked downstream so
forward-path outcomes are testable without a real downstream service.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from pokeproxy.logging_config import setup_logging

# Imported at module scope on purpose: pokeproxy.main calls setup_logging() at
# import time, which would otherwise tear down the capture buffer installed by
# the `logs` fixture, depending on which test ran first.
from pokeproxy.main import app
from pokeproxy.proto import pokemon_pb2

DEV_SECRET_B64 = "dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg=="  # noqa: S105 — local dev key


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """Capture JSON log output, with the app configured to a working state."""
    monkeypatch.setenv("POKEPROXY_HMAC_KEY", DEV_SECRET_B64)
    monkeypatch.setenv("POKEPROXY_CONFIG", "config/rules.json")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    buffer = io.StringIO()
    setup_logging(stream=buffer)
    yield buffer
    setup_logging()  # restore a normal handler for other tests


@pytest.fixture
def no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the cache so tests don't depend on a real Redis connection."""

    async def _miss(redis: object, cache_key: str, metrics: object) -> None:
        return None

    async def _store(
        redis: object,
        cache_key: str,
        status_code: int,
        headers: dict[str, str],
        content: bytes,
        ttl_seconds: float,
        metrics: object,
    ) -> None:
        return None

    monkeypatch.setattr("pokeproxy.proxy.get_cached_response", _miss)
    monkeypatch.setattr("pokeproxy.proxy.cache_response", _store)


def _json_records(buffer: io.StringIO) -> list[dict]:
    """Parse the JSON records, skipping traceback text lines.

    Tracebacks are deliberately emitted as plain multi-line text after their
    JSON record, so not every line in the stream is an object.
    """
    records = []
    for line in buffer.getvalue().splitlines():
        if not line.startswith("{"):
            continue
        records.append(json.loads(line))
    return records


def _access_lines(buffer: io.StringIO) -> list[dict]:
    return [
        r for r in _json_records(buffer)
        if r.get("msg") in {"request", "request failed"}
    ]


def _pikachu() -> bytes:
    """A payload that deliberately matches no rule in config/rules.json."""
    pokemon = pokemon_pb2.Pokemon()
    fields = {
        "number": 25, "name": "Pikachu", "type_one": "Electric", "type_two": "",
        "total": 320, "hit_points": 35, "attack": 55, "defense": 40,
        "special_attack": 50, "special_defense": 50, "speed": 90,
        "generation": 1, "legendary": False,
    }
    for key, value in fields.items():
        setattr(pokemon, key, value)
    return pokemon.SerializeToString()


def _sign(body: bytes) -> str:
    return hmac.new(base64.b64decode(DEV_SECRET_B64), body, hashlib.sha256).hexdigest()


def _legendary_pokemon() -> bytes:
    pokemon = pokemon_pb2.Pokemon()
    fields = {
        "number": 150, "name": "Mewtwo", "type_one": "Psychic", "type_two": "",
        "total": 680, "hit_points": 106, "attack": 110, "defense": 90,
        "special_attack": 154, "special_defense": 90, "speed": 130,
        "generation": 1, "legendary": True,
    }
    for key, value in fields.items():
        setattr(pokemon, key, value)
    return pokemon.SerializeToString()


DownstreamHandler = Callable[[httpx.Request], Awaitable[httpx.Response]]


@contextmanager
def _client_with_downstream(handler: DownstreamHandler) -> Iterator[TestClient]:
    with TestClient(app) as client:
        client.app.state.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        yield client


# --- formatter ------------------------------------------------------------


def test_output_is_one_json_object_per_line() -> None:
    buffer = io.StringIO()
    setup_logging(stream=buffer)
    try:
        logging.getLogger("pokeproxy").info("hello", extra={"custom": 42})
    finally:
        setup_logging()

    lines = [ln for ln in buffer.getvalue().splitlines() if ln]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["msg"] == "hello"
    assert record["level"] == "INFO"
    assert record["custom"] == 42
    assert "ts" in record


def test_exception_emits_a_json_record_followed_by_a_readable_traceback() -> None:
    """Tracebacks stay multi-line on purpose — an escaped one is unreadable."""
    buffer = io.StringIO()
    setup_logging(stream=buffer)
    try:
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("pokeproxy").exception("it failed")
    finally:
        setup_logging()

    lines = buffer.getvalue().splitlines()

    # First line is the structured record, carrying a short summary.
    record = json.loads(lines[0])
    assert record["msg"] == "it failed"
    assert record["level"] == "ERROR"
    assert record["error"] == "ValueError: boom"

    # The traceback follows as ordinary text, not escaped into the object.
    traceback_text = "\n".join(lines[1:])
    assert traceback_text.startswith("Traceback (most recent call last):")
    assert "ValueError: boom" in traceback_text
    assert "\n" not in lines[0], "traceback must not be escaped into the JSON"


# --- correlation ----------------------------------------------------------


def test_request_id_generated_when_absent(logs: io.StringIO) -> None:
    with TestClient(app) as client:
        response = client.post("/stream", content=b"x")

    assert response.headers["X-Request-ID"]
    assert _access_lines(logs)[0]["request_id"] == response.headers["X-Request-ID"]


def test_supplied_request_id_is_echoed_and_logged(logs: io.StringIO) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/stream", content=b"x", headers={"X-Request-ID": "trace-me-123"}
        )

    assert response.headers["X-Request-ID"] == "trace-me-123"
    assert _access_lines(logs)[0]["request_id"] == "trace-me-123"


def test_health_is_not_access_logged(logs: io.StringIO) -> None:
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert _access_lines(logs) == []


def test_ready_is_not_access_logged(logs: io.StringIO) -> None:
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200

    assert _access_lines(logs) == []


# --- outcomes -------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "outcome"),
    [
        ({}, "rejected_signature_missing"),
        ({"X-Grd-Signature": "deadbeef"}, "rejected_signature_invalid"),
    ],
)
def test_signature_failures_are_distinguishable(
    logs: io.StringIO, headers: dict[str, str], outcome: str
) -> None:
    """The exact gap C5 was opened for: both used to log identically."""
    with TestClient(app) as client:
        response = client.post("/stream", content=b"anything", headers=headers)

    assert response.status_code == 401
    lines = _access_lines(logs)
    assert len(lines) == 1, "exactly one access line per request"
    assert lines[0]["outcome"] == outcome
    assert lines[0]["status"] == 401


def test_no_rule_matched_is_visible(logs: io.StringIO, no_cache: None) -> None:
    """Returns 200 with an empty body — only `outcome` distinguishes it."""
    body = _pikachu()
    with TestClient(app) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )

    assert response.status_code == 200
    assert response.json() == {}
    assert _access_lines(logs)[0]["outcome"] == "no_rule_matched"


def test_invalid_protobuf_outcome(logs: io.StringIO, no_cache: None) -> None:
    body = b"\xff\xff\xff\xff not protobuf"
    with TestClient(app) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )

    assert response.status_code == 400
    assert _access_lines(logs)[0]["outcome"] == "rejected_protobuf"


def test_payload_too_large_outcome(logs: io.StringIO) -> None:
    body = b"x" * 1_048_577
    with TestClient(app) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )

    assert response.status_code == 413
    assert _access_lines(logs)[0]["outcome"] == "rejected_too_large"


def test_access_line_carries_method_path_and_duration(logs: io.StringIO) -> None:
    with TestClient(app) as client:
        client.post("/stream", content=b"x")

    line = _access_lines(logs)[0]
    assert line["method"] == "POST"
    assert line["path"] == "/stream"
    assert isinstance(line["duration_ms"], int | float)


def test_forwarded_outcome(logs: io.StringIO, no_cache: None) -> None:
    async def accept(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    with _client_with_downstream(accept) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )

    assert response.status_code == 200
    assert _access_lines(logs)[0]["outcome"] == "forwarded"


def test_downstream_timeout_outcome(
    logs: io.StringIO, no_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORWARD_MAX_ATTEMPTS", "1")

    async def always_time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("downstream did not respond", request=request)

    body = _legendary_pokemon()
    with _client_with_downstream(always_time_out) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )

    assert response.status_code == 504
    assert _access_lines(logs)[0]["outcome"] == "downstream_timeout"


def test_downstream_error_outcome(
    logs: io.StringIO, no_cache: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORWARD_MAX_ATTEMPTS", "1")

    async def always_refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    body = _legendary_pokemon()
    with _client_with_downstream(always_refuse) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )

    assert response.status_code == 502
    assert _access_lines(logs)[0]["outcome"] == "downstream_error"


def test_secrets_never_appear_in_logs(logs: io.StringIO, no_cache: None) -> None:
    body = _pikachu()
    signature = _sign(body)
    with TestClient(app) as client:
        client.post("/stream", content=body, headers={"X-Grd-Signature": signature})

    output = logs.getvalue()
    assert signature not in output
    assert DEV_SECRET_B64 not in output
    assert "test-secret-for-local-dev" not in output
