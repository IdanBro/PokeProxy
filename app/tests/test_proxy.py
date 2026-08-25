from __future__ import annotations

import asyncio
import itertools

import httpx
import pytest

from pokeproxy import proxy
from pokeproxy.metrics import Metrics
from pokeproxy.proxy import (
    MAX_BODY_SIZE,
    DownstreamResponseTooLarge,
    RetryPolicy,
    _forward_with_retry,
)

_METRICS = Metrics.create(revision="test", version="test")


class CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self, outcomes: list[httpx.Response | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.call_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.call_count += 1
        outcome = self.outcomes[min(self.call_count, len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def always_connect_error() -> Exception:
    return httpx.ConnectError("connection refused")


def always_read_timeout() -> Exception:
    return httpx.ReadTimeout("timed out")


def success_response() -> httpx.Response:
    return httpx.Response(200, json={"status": "received"})


def oversized_response(size: int = MAX_BODY_SIZE + 1) -> httpx.Response:
    return httpx.Response(200, content=b"x" * size)


async def _run_retry(
    transport: CountingTransport, policy: RetryPolicy
) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport) as client:
        return await _forward_with_retry(
            client,
            policy,
            "http://downstream.invalid/pokemon",
            b"{}",
            {},
            _METRICS,
            "test rule",
        )


def test_succeeds_without_retrying_on_first_success() -> None:
    transport = CountingTransport([success_response()])
    policy = RetryPolicy(max_attempts=5, deadline_seconds=5.0)

    response = asyncio.run(_run_retry(transport, policy))

    assert response.status_code == 200
    assert transport.call_count == 1


def test_recovers_after_transient_failures_within_the_attempt_cap() -> None:
    transport = CountingTransport(
        [always_connect_error(), always_connect_error(), success_response()]
    )
    policy = RetryPolicy(max_attempts=5, deadline_seconds=5.0)

    response = asyncio.run(_run_retry(transport, policy))

    assert response.status_code == 200
    assert transport.call_count == 3


def test_gives_up_after_max_attempts() -> None:
    transport = CountingTransport([always_connect_error()])
    policy = RetryPolicy(max_attempts=3, deadline_seconds=30.0)

    with pytest.raises(httpx.ConnectError):
        asyncio.run(_run_retry(transport, policy))

    assert transport.call_count == 3


def test_last_error_type_is_preserved_when_attempts_are_exhausted() -> None:
    transport = CountingTransport([always_read_timeout()])
    policy = RetryPolicy(max_attempts=2, deadline_seconds=30.0)

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(_run_retry(transport, policy))


def test_stops_when_deadline_is_exceeded_before_attempts_are_exhausted() -> None:
    fake_clock = itertools.chain([0.0, 0.0, 10.0], itertools.repeat(10.0))

    async def no_wait(_: float) -> None:
        return None

    transport = CountingTransport([always_connect_error()])
    policy = RetryPolicy(max_attempts=50, deadline_seconds=5.0)

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport) as client:
            return await proxy._forward_with_retry(
                client,
                policy,
                "http://downstream.invalid/pokemon",
                b"{}",
                {},
                _METRICS,
                "test rule",
                clock=lambda: next(fake_clock),
                sleep=no_wait,
            )

    with pytest.raises(httpx.ConnectError):
        asyncio.run(run())

    assert transport.call_count == 2


def test_reuses_the_provided_client_without_constructing_a_new_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = CountingTransport(
        [always_connect_error(), always_connect_error(), success_response()]
    )
    client = httpx.AsyncClient(transport=transport)

    construction_count = 0
    original_init = httpx.AsyncClient.__init__

    def counting_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        nonlocal construction_count
        construction_count += 1
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", counting_init)

    policy = RetryPolicy(max_attempts=5, deadline_seconds=5.0)
    asyncio.run(_run_retry_with_client(client, policy))

    assert construction_count == 0


async def _run_retry_with_client(
    client: httpx.AsyncClient, policy: RetryPolicy
) -> httpx.Response:
    return await _forward_with_retry(
        client,
        policy,
        "http://downstream.invalid/pokemon",
        b"{}",
        {},
        _METRICS,
        "test rule",
    )


def test_downstream_response_over_the_size_cap_is_not_retried() -> None:
    transport = CountingTransport([oversized_response()])
    policy = RetryPolicy(max_attempts=5, deadline_seconds=5.0)

    with pytest.raises(DownstreamResponseTooLarge):
        asyncio.run(_run_retry(transport, policy))

    assert transport.call_count == 1


def test_downstream_response_at_exactly_the_size_cap_is_accepted() -> None:
    transport = CountingTransport([oversized_response(size=MAX_BODY_SIZE)])
    policy = RetryPolicy(max_attempts=5, deadline_seconds=5.0)

    response = asyncio.run(_run_retry(transport, policy))

    assert response.status_code == 200
    assert len(response.content) == MAX_BODY_SIZE


def test_first_attempt_never_sleeps() -> None:
    async def fail_if_called(_: float) -> None:
        raise AssertionError("a successful first attempt must not back off")

    transport = CountingTransport([success_response()])
    policy = RetryPolicy(max_attempts=3, deadline_seconds=5.0)

    async def run() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport) as client:
            return await proxy._forward_with_retry(
                client,
                policy,
                "http://downstream.invalid/pokemon",
                b"{}",
                {},
                _METRICS,
                "test rule",
                sleep=fail_if_called,
            )

    asyncio.run(run())
