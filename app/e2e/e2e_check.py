#!/usr/bin/env python3
"""Post-deploy E2E check.

Sends real signed protobuf traffic through the proxy and confirms it was
actually delivered to the downstream, not just accepted. Run as a Helm/Argo
CD hook Job against a live deployment.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import random
import sys
import time
import uuid
from typing import Any

import httpx

from pokeproxy.proto import pokemon_pb2

NEGATIVE_CHECK_ATTEMPTS = 3
NEGATIVE_CHECK_INTERVAL_SECONDS = 0.3
POLL_INTERVAL_SECONDS = 0.4
STARTUP_MAX_WAIT_SECONDS = 90
STARTUP_POLL_INTERVAL_SECONDS = 2.0


class CheckFailed(Exception):
    pass


def sign(secret: bytes, body: bytes) -> str:
    return hmac.new(key=secret, msg=body, digestmod=hashlib.sha256).hexdigest()


def make_pokemon(**overrides: Any) -> tuple[bytes, str]:
    fields = {
        "number": random.randint(900000, 999999),  # noqa: S311
        "name": f"e2e-{uuid.uuid4().hex[:8]}",
        "type_one": "Normal",
        "type_two": "",
        "total": 300,
        "hit_points": 50,
        "attack": 50,
        "defense": 50,
        "special_attack": 50,
        "special_defense": 50,
        "speed": 50,
        "generation": 5,
        "legendary": False,
    }
    fields.update(overrides)
    proto = pokemon_pb2.Pokemon()
    for key, value in fields.items():
        setattr(proto, key, value)
    return proto.SerializeToString(), fields["name"]


def post_stream(
    client: httpx.Client, proxy_url: str, body: bytes, signature: str
) -> httpx.Response:
    return client.post(
        proxy_url,
        content=body,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Grd-Signature": signature,
        },
    )


def find_received(client: httpx.Client, mock_url: str, name: str) -> dict[str, Any] | None:
    resp = client.get(f"{mock_url}/received")
    resp.raise_for_status()
    for record in resp.json():
        if record.get("pokemon", {}).get("name") == name:
            return record
    return None


def wait_for_received(
    client: httpx.Client, mock_url: str, name: str, attempts: int, interval: float
) -> dict[str, Any] | None:
    for _ in range(attempts):
        record = find_received(client, mock_url, name)
        if record is not None:
            return record
        time.sleep(interval)
    return None


def wait_until_reachable(client: httpx.Client, proxy_url: str) -> None:
    """A fresh deploy's PostSync/post-install hook can fire before the app's
    own pods are Ready — a Service with no ready endpoints refuses the
    connection rather than timing out. Retry until something answers."""
    deadline = time.monotonic() + STARTUP_MAX_WAIT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client.post(proxy_url, content=b"", headers={"X-Grd-Signature": "warmup"})
            return
        except httpx.ConnectError as e:
            last_error = e
            time.sleep(STARTUP_POLL_INTERVAL_SECONDS)
    raise CheckFailed(f"{proxy_url} never became reachable: {last_error}")


def run_checks(
    client: httpx.Client, proxy_url: str, mock_url: str, secret: bytes, retries: int
) -> list[str]:
    passed: list[str] = []

    wait_until_reachable(client, proxy_url)
    passed.append("proxy reachable")

    match_body, match_name = make_pokemon(type_one="Fire", attack=95, generation=1)
    resp = post_stream(client, proxy_url, match_body, sign(secret, match_body))
    if resp.status_code != 200 or resp.json() != {"status": "received"}:
        raise CheckFailed(
            f"matching payload: expected 200 {{'status': 'received'}}, "
            f"got {resp.status_code} {resp.text!r}"
        )
    passed.append("signed matching payload forwarded (200)")

    record = wait_for_received(client, mock_url, match_name, retries, POLL_INTERVAL_SECONDS)
    if record is None:
        raise CheckFailed(f"{match_name!r} never appeared in {mock_url}/received")
    if record.get("reason") != "strong fire pokemon":
        raise CheckFailed(f"expected reason 'strong fire pokemon', got {record.get('reason')!r}")
    passed.append("delivered to mock downstream with correct reason")

    nomatch_body, nomatch_name = make_pokemon(
        type_one="Water", attack=10, defense=10, hit_points=10, generation=6
    )
    resp = post_stream(client, proxy_url, nomatch_body, sign(secret, nomatch_body))
    if resp.status_code != 200 or resp.json() != {}:
        raise CheckFailed(
            f"non-matching payload: expected 200 {{}}, got {resp.status_code} {resp.text!r}"
        )
    record = wait_for_received(
        client, mock_url, nomatch_name, NEGATIVE_CHECK_ATTEMPTS, NEGATIVE_CHECK_INTERVAL_SECONDS
    )
    if record is not None:
        raise CheckFailed(f"non-matching payload {nomatch_name!r} unexpectedly reached mock")
    passed.append("non-matching payload not forwarded")

    bad_body, _ = make_pokemon()
    good_sig = sign(secret, bad_body)
    corrupted_sig = ("0" if good_sig[0] != "0" else "1") + good_sig[1:]
    resp = post_stream(client, proxy_url, bad_body, corrupted_sig)
    if resp.status_code != 401:
        raise CheckFailed(f"corrupted signature: expected 401, got {resp.status_code}")
    passed.append("corrupted signature rejected (401)")

    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description="PokeProxy post-deploy E2E check")
    parser.add_argument("--proxy-url", required=True)
    parser.add_argument("--mock-url", required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=5)
    args = parser.parse_args()

    raw_secret = os.environ.get("POKEPROXY_HMAC_KEY")
    if not raw_secret:
        print(json.dumps({"result": "fail", "error": "POKEPROXY_HMAC_KEY not set"}))
        sys.exit(1)
    secret = base64.b64decode(raw_secret)

    try:
        with httpx.Client(timeout=args.timeout) as client:
            checks = run_checks(client, args.proxy_url, args.mock_url, secret, args.retries)
    except (CheckFailed, httpx.HTTPError) as e:
        print(json.dumps({"result": "fail", "error": str(e)}))
        sys.exit(1)

    print(json.dumps({"result": "pass", "checks": checks}))


if __name__ == "__main__":
    main()
