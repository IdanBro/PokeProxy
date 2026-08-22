"""Configuration validation tests.

Regression cover for C1: the service must start from its own documented
configuration, and must refuse an HMAC key that is absent, malformed, or
trivially weak instead of coming up with authentication silently disabled.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from pokeproxy.config import MIN_HMAC_KEY_BYTES, Settings

DEV_SECRET_B64 = "dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg=="  # noqa: S105 — decodes to the 25-byte dev key
ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings reads the process environment; clear it so tests are hermetic."""
    for key in list(os.environ):
        if key.upper().startswith(("POKEPROXY_", "REDIS_")):
            monkeypatch.delenv(key, raising=False)


def _build(**overrides: str) -> Settings:
    """Construct Settings without reading any local .env file."""
    kwargs: dict[str, str] = {"pokeproxy_config": "config/rules.json", **overrides}
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


def test_starts_from_env_example_unmodified() -> None:
    """A clean checkout must boot from .env.example with no edits.

    This is the headline C1 regression: the file used to define
    POKEPROXY_SECRET while the code read POKEPROXY_HMAC_KEY.
    """
    documented = {k.lower(): v for k, v in _parse_env_file(ENV_EXAMPLE).items()}
    assert "pokeproxy_hmac_key" in documented, (
        ".env.example must define POKEPROXY_HMAC_KEY — the variable the code reads"
    )

    settings = Settings(_env_file=None, **documented)  # type: ignore[call-arg]
    assert settings.hmac_key == b"test-secret-for-local-dev"


def test_accepts_valid_key() -> None:
    settings = _build(pokeproxy_hmac_key=DEV_SECRET_B64)
    assert len(settings.hmac_key) >= MIN_HMAC_KEY_BYTES


def test_missing_key_names_the_variable() -> None:
    with pytest.raises(ValidationError) as exc:
        _build()
    assert "pokeproxy_hmac_key" in str(exc.value).lower()


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("changeme", "unreplaced placeholder — valid base64, but only 6 bytes"),
        ("", "variable set but empty — decodes to a zero-length key"),
        ("YQ==", "single-byte key"),
        ("aGVsbG8=", "5-byte key, just under the floor"),
    ],
)
def test_rejects_weak_key(value: str, why: str) -> None:
    with pytest.raises(ValidationError) as exc:
        _build(pokeproxy_hmac_key=value)
    message = str(exc.value)
    assert "POKEPROXY_HMAC_KEY" in message, why
    assert "openssl rand -base64 32" in message, "error must tell the operator what to do"


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("abcd efgh", "embedded space would be silently discarded by lax decoding"),
        ("not-base64-at-all!!", "non-base64 alphabet"),
        ("dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg", "correct key, padding stripped"),
    ],
)
def test_rejects_malformed_base64(value: str, why: str) -> None:
    with pytest.raises(ValidationError) as exc:
        _build(pokeproxy_hmac_key=value)
    assert "POKEPROXY_HMAC_KEY" in str(exc.value), why


def test_boundary_key_is_accepted() -> None:
    """Exactly MIN_HMAC_KEY_BYTES must pass — the floor is inclusive."""
    import base64

    exact = base64.b64encode(b"x" * MIN_HMAC_KEY_BYTES).decode()
    assert len(_build(pokeproxy_hmac_key=exact).hmac_key) == MIN_HMAC_KEY_BYTES

    one_short = base64.b64encode(b"x" * (MIN_HMAC_KEY_BYTES - 1)).decode()
    with pytest.raises(ValidationError):
        _build(pokeproxy_hmac_key=one_short)
