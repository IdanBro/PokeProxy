"""Rules-loading regression tests for H1.

The rules file must be loaded and validated exactly once, at startup, not
re-read from disk on every request. A broken rules file must fail the
process at startup, not surface as a request-time crash.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pokeproxy import main
from pokeproxy.main import _load_rules, app
from pokeproxy.proto import pokemon_pb2

DEV_SECRET_B64 = "dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg=="  # noqa: S105 — local dev key


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEPROXY_HMAC_KEY", DEV_SECRET_B64)
    monkeypatch.setenv("POKEPROXY_CONFIG", "config/rules.json")


@pytest.fixture
def no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _miss(redis: object, cache_key: str) -> None:
        return None

    async def _store(
        redis: object,
        cache_key: str,
        status_code: int,
        headers: dict[str, str],
        content: bytes,
        ttl_seconds: float,
    ) -> None:
        return None

    monkeypatch.setattr("pokeproxy.proxy.get_cached_response", _miss)
    monkeypatch.setattr("pokeproxy.proxy.cache_response", _store)


def _sign(body: bytes) -> str:
    return hmac.new(base64.b64decode(DEV_SECRET_B64), body, hashlib.sha256).hexdigest()


def _pikachu_bytes() -> bytes:
    """Deliberately matches no rule in config/rules.json."""
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


def test_app_state_holds_the_loaded_rules() -> None:
    with TestClient(app) as client:
        assert len(client.app.state.rules) == 3


def test_rules_file_is_read_once_regardless_of_request_count(
    monkeypatch: pytest.MonkeyPatch, no_cache: None
) -> None:
    call_count = 0
    original_load_rules = main.load_rules

    def counting_load_rules(config_path: str):
        nonlocal call_count
        call_count += 1
        return original_load_rules(config_path)

    monkeypatch.setattr(main, "load_rules", counting_load_rules)

    body = _pikachu_bytes()
    with TestClient(app) as client:
        for _ in range(3):
            client.post("/stream", content=body, headers={"X-Grd-Signature": _sign(body)})

    assert call_count == 1


def test_invalid_rule_fails_startup_not_the_first_request(tmp_path: Path) -> None:
    bad_file = tmp_path / "rules.json"
    bad_file.write_text(json.dumps({"rules": [{"url": "not-a-url", "match": ["a==1"]}]}))

    with pytest.raises(SystemExit):
        _load_rules(str(bad_file))


def test_malformed_json_fails_startup(tmp_path: Path) -> None:
    bad_file = tmp_path / "rules.json"
    bad_file.write_text("not json")

    with pytest.raises(SystemExit):
        _load_rules(str(bad_file))


def test_missing_rules_file_fails_startup(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.json"

    with pytest.raises(SystemExit):
        _load_rules(str(missing))


def test_startup_failure_is_logged_as_critical(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "does-not-exist.json"

    with caplog.at_level(logging.CRITICAL, logger="pokeproxy"), pytest.raises(SystemExit):
        _load_rules(str(missing))

    assert "rules configuration invalid" in caplog.text
