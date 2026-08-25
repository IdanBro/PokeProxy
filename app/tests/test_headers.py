"""Header-hygiene regression tests for H2 + H3.

H3 — only an explicit allowlist of client headers may reach downstream; the
proxy builds every header downstream actually needs itself.
H2 — hop-by-hop headers on the downstream response (framing, connection
management, encoding) must not be relayed to the client verbatim.
"""

from __future__ import annotations

import httpx

from pokeproxy.proxy import _build_forward_headers, _forwardable_response_headers

from .conftest import _client_with_downstream, _legendary_pokemon, _sign


def test_forward_headers_are_built_from_scratch() -> None:
    headers = _build_forward_headers("strong fire pokemon", "req-2")

    assert set(headers) == {"Content-Type", "X-Grd-Reason", "X-Request-ID"}
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Grd-Reason"] == "strong fire pokemon"
    assert headers["X-Request-ID"] == "req-2"


def test_hop_by_hop_response_headers_are_stripped() -> None:
    headers = httpx.Headers(
        [
            ("Content-Type", "application/json"),
            ("Content-Length", "123"),
            ("Content-Encoding", "gzip"),
            ("Transfer-Encoding", "chunked"),
            ("Connection", "keep-alive"),
            ("Keep-Alive", "timeout=5"),
        ]
    )

    result = _forwardable_response_headers(headers)

    assert result == {"content-type": "application/json"}


def test_non_hop_by_hop_response_headers_pass_through() -> None:
    headers = httpx.Headers(
        [("Content-Type", "application/json"), ("X-Downstream-Trace", "abc123")]
    )

    result = _forwardable_response_headers(headers)

    assert result["x-downstream-trace"] == "abc123"


def test_client_headers_are_not_relayed_downstream_end_to_end(no_cache: None) -> None:
    received: dict[str, str] = {}

    async def capture(request: httpx.Request) -> httpx.Response:
        received.update(request.headers)
        return httpx.Response(200, json={"status": "received"})

    body = _legendary_pokemon()
    with _client_with_downstream(capture) as client:
        client.post(
            "/stream",
            content=body,
            headers={
                "X-Grd-Signature": _sign(body),
                "Authorization": "Bearer should-not-leak",
                "Cookie": "session=should-not-leak",
            },
        )

    assert "authorization" not in received
    assert "cookie" not in received
    assert received["x-grd-reason"] == "legendary pokemon"


def test_downstream_hop_by_hop_headers_are_not_relayed_to_client(
    no_cache: None,
) -> None:
    async def respond_with_framing_headers(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "received"},
            headers={"Connection": "keep-alive", "X-Downstream-Trace": "abc123"},
        )

    body = _legendary_pokemon()
    with _client_with_downstream(respond_with_framing_headers) as client:
        response = client.post(
            "/stream", content=body, headers={"X-Grd-Signature": _sign(body)}
        )

    assert "connection" not in response.headers
    assert response.headers["x-downstream-trace"] == "abc123"
