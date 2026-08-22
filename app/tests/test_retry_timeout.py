"""R1 regression: a slow/hung downstream must not swallow the whole retry
budget in a single attempt.

Uses a real TCP socket that accepts and never responds, not a mock
transport — httpx only enforces `read`/`write` timeouts for real network
I/O; a custom `httpx.AsyncBaseTransport` that returns instantly (as used
elsewhere in this suite) bypasses that machinery entirely and would prove
nothing here. This mirrors the manual hung-Redis verification technique used
for C4 (see `docs/issues/005-unguarded-redis-calls.md`), promoted to an
automated test.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import httpx
import pytest

from pokeproxy.config import Settings
from pokeproxy.main import _build_http_client
from pokeproxy.proxy import RetryPolicy, _forward_with_retry


class _HungServer:
    """Accepts TCP connections and never writes a byte back."""

    def __init__(self) -> None:
        self.accepted_connections = 0
        self._server: asyncio.AbstractServer | None = None
        self._handler_tasks: list[asyncio.Task[None]] = []

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.accepted_connections += 1
        self._handler_tasks.append(asyncio.current_task())  # type: ignore[arg-type]
        # Never read, never write, never close — the client must time out.
        await asyncio.sleep(3600)

    async def __aenter__(self) -> _HungServer:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        # `Server.wait_closed()` (Python 3.13+) also waits for every accepted
        # connection's handler to finish — ours never does, so cancel the
        # still-sleeping handlers first instead of waiting on them.
        for task in self._handler_tasks:
            task.cancel()
        for task in self._handler_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with contextlib.suppress(Exception):
            await self._server.wait_closed()

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.sockets[0].getsockname()[:2]
        return f"http://{host}:{port}/pokemon"


def _settings(**overrides: str) -> Settings:
    kwargs: dict[str, str] = {
        "pokeproxy_hmac_key": "dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg==",
        "pokeproxy_config": "config/rules.json",
        **overrides,
    }
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


async def test_hung_downstream_is_retried_within_the_deadline() -> None:
    """Before the R1 fix, `read` timeout == `forward_deadline_seconds`, so a
    hung downstream consumed the entire budget in attempt 1 (measured: 1 of 3
    attempts, 10.17s against the default config). With attempt_timeout <
    deadline, the same hang must yield more than one attempt.
    """
    settings = _settings(
        forward_attempt_timeout_seconds="0.3", forward_deadline_seconds="1.0"
    )
    client = _build_http_client(settings)
    policy = RetryPolicy(max_attempts=10, deadline_seconds=1.0)

    async with _HungServer() as server, client:
        start = time.monotonic()
        with pytest.raises(httpx.TimeoutException):
            await _forward_with_retry(client, policy, server.url, b"{}", {})
        elapsed = time.monotonic() - start

        assert server.accepted_connections >= 2, (
            "a hung downstream should be retried more than once within the "
            f"deadline; only {server.accepted_connections} connection(s) were "
            "accepted (R1 regression)"
        )
        assert elapsed < policy.deadline_seconds + 0.5, (
            f"retry loop overran its deadline: {elapsed:.2f}s"
        )


async def test_client_read_write_timeout_matches_attempt_timeout_not_deadline() -> None:
    settings = _settings(
        forward_attempt_timeout_seconds="3.0", forward_deadline_seconds="10.0"
    )
    client = _build_http_client(settings)
    async with client:
        assert client.timeout.read == 3.0
        assert client.timeout.write == 3.0
        assert client.timeout.connect == 5.0
