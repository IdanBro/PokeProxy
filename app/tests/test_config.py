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
        if key.upper().startswith(("POKEPROXY_", "REDIS_", "FORWARD_", "LOG_")):
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


@pytest.mark.parametrize(
    ("env_var", "value", "attr", "expected"),
    [
        ("FORWARD_MAX_ATTEMPTS", "9", "forward_max_attempts", 9),
        ("FORWARD_DEADLINE_SECONDS", "42.0", "forward_deadline_seconds", 42.0),
        (
            "FORWARD_ATTEMPT_TIMEOUT_SECONDS",
            "4.0",
            "forward_attempt_timeout_seconds",
            4.0,
        ),
        ("REDIS_CONNECT_TIMEOUT_SECONDS", "7.5", "redis_connect_timeout_seconds", 7.5),
        ("REDIS_SOCKET_TIMEOUT_SECONDS", "8.5", "redis_socket_timeout_seconds", 8.5),
        ("CACHE_TTL_SECONDS", "120.0", "cache_ttl_seconds", 120.0),
    ],
)
def test_operational_settings_are_configurable_via_env(
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
    value: str,
    attr: str,
    expected: float,
) -> None:
    monkeypatch.setenv(env_var, value)
    settings = _build(pokeproxy_hmac_key=DEV_SECRET_B64)
    assert getattr(settings, attr) == expected


@pytest.mark.parametrize(
    ("env_var", "value"),
    [
        ("FORWARD_MAX_ATTEMPTS", "0"),
        ("FORWARD_DEADLINE_SECONDS", "0"),
        ("FORWARD_ATTEMPT_TIMEOUT_SECONDS", "0"),
        ("REDIS_CONNECT_TIMEOUT_SECONDS", "0"),
        ("REDIS_SOCKET_TIMEOUT_SECONDS", "-1"),
        ("CACHE_TTL_SECONDS", "0"),
    ],
)
def test_operational_settings_reject_non_positive_values(
    monkeypatch: pytest.MonkeyPatch, env_var: str, value: str
) -> None:
    monkeypatch.setenv(env_var, value)
    with pytest.raises(ValidationError) as exc:
        _build(pokeproxy_hmac_key=DEV_SECRET_B64)
    assert env_var in str(exc.value)


@pytest.mark.parametrize(
    ("attempt_timeout", "deadline"),
    [
        ("10.0", "10.0"),  # equal — exactly the R1 default-config bug
        ("15.0", "10.0"),  # attempt timeout longer than the whole budget
    ],
)
def test_rejects_attempt_timeout_not_less_than_deadline(
    monkeypatch: pytest.MonkeyPatch, attempt_timeout: str, deadline: str
) -> None:
    """R1: an attempt timeout >= the deadline lets one slow attempt eat the
    whole retry budget, so FORWARD_MAX_ATTEMPTS never gets a chance to retry.
    """
    monkeypatch.setenv("FORWARD_ATTEMPT_TIMEOUT_SECONDS", attempt_timeout)
    monkeypatch.setenv("FORWARD_DEADLINE_SECONDS", deadline)
    with pytest.raises(ValidationError) as exc:
        _build(pokeproxy_hmac_key=DEV_SECRET_B64)
    assert "FORWARD_ATTEMPT_TIMEOUT_SECONDS" in str(exc.value)
    assert "FORWARD_DEADLINE_SECONDS" in str(exc.value)


def test_accepts_attempt_timeout_strictly_less_than_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORWARD_ATTEMPT_TIMEOUT_SECONDS", "3.0")
    monkeypatch.setenv("FORWARD_DEADLINE_SECONDS", "10.0")
    settings = _build(pokeproxy_hmac_key=DEV_SECRET_B64)
    assert settings.forward_attempt_timeout_seconds == 3.0


def test_scratch_prove_ci_goes_red():
    assert False, "scratch commit for CI step-1 verification, to be reverted"
