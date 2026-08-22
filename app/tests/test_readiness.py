"""Liveness/readiness split regression tests for M1 + H7.

`/health` answers "is the process responsive" and never varies. `/ready`
answers "should traffic be routed here right now" — true once startup has
finished, false the moment shutdown begins, so a readiness probe polled
during a rollout reports the pod as not ready instead of lying.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pokeproxy.main import app

DEV_SECRET_B64 = "dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg=="  # noqa: S105 — local dev key


@pytest.fixture(autouse=True)
def _configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POKEPROXY_HMAC_KEY", DEV_SECRET_B64)
    monkeypatch.setenv("POKEPROXY_CONFIG", "config/rules.json")


def test_ready_after_startup() -> None:
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_not_ready_returns_503() -> None:
    with TestClient(app) as client:
        client.app.state.ready = False
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not ready"}


def test_ready_flag_is_false_after_shutdown() -> None:
    with TestClient(app):
        pass

    assert app.state.ready is False


def test_health_is_unaffected_by_readiness_state() -> None:
    with TestClient(app) as client:
        client.app.state.ready = False
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
