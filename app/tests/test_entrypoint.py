"""Regression cover for R4 and M6, closed by `python -m pokeproxy`.

R4: a bad configuration under the FastAPI lifespan produces the intended
CRITICAL line and then uvicorn's own ~20-line lifespan SystemExit traceback.
The entrypoint validates configuration before uvicorn starts, so the failure
is one line and nothing else.

M6: `POKEPROXY_PORT` was documented and validated but nothing read it — the
real port came from the CLI's hardcoded `--port 8000`. The entrypoint is the
one place that reads it.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import Mock

import pytest

from pokeproxy.__main__ import main

DEV_SECRET_B64 = "dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg=="  # noqa: S105 — local dev key


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEPROXY_HMAC_KEY", DEV_SECRET_B64)
    monkeypatch.setenv("POKEPROXY_CONFIG", "config/rules.json")


def test_bad_config_exits_before_uvicorn_runs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("POKEPROXY_HMAC_KEY", raising=False)
    run = Mock()
    monkeypatch.setattr("pokeproxy.__main__.uvicorn.run", run)

    with caplog.at_level(logging.CRITICAL, logger="pokeproxy"), pytest.raises(SystemExit):
        main()

    run.assert_not_called()
    assert "configuration invalid" in caplog.text


def test_configured_port_reaches_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEPROXY_PORT", "9001")
    run = Mock()
    monkeypatch.setattr("pokeproxy.__main__.uvicorn.run", run)

    main()

    run.assert_called_once()
    _args, kwargs = run.call_args
    assert kwargs["port"] == 9001
    assert kwargs["host"] == "0.0.0.0"  # noqa: S104 — asserting the entrypoint's actual bind


def test_default_port_is_8000(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr("pokeproxy.__main__.uvicorn.run", run)

    main()

    _args, kwargs = run.call_args
    assert kwargs["port"] == 8000


def test_app_import_string_is_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr("pokeproxy.__main__.uvicorn.run", run)

    main()

    args: tuple[Any, ...] = run.call_args[0]
    assert args[0] == "pokeproxy.main:app"


def test_uvicorn_logging_config_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """`log_config=None` stops uvicorn from re-clobbering the JSON logging
    already installed on import of `pokeproxy.main` — see the module docstring.
    """
    run = Mock()
    monkeypatch.setattr("pokeproxy.__main__.uvicorn.run", run)

    main()

    _args, kwargs = run.call_args
    assert kwargs["log_config"] is None
