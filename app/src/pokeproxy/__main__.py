"""Container entrypoint: validate configuration, then hand off to uvicorn.

`pokeproxy.main` builds `Settings` lazily, inside the FastAPI lifespan, so a
bad configuration under `uvicorn pokeproxy.main:app` fails only after uvicorn
has already started its own asyncio machinery — the operator sees the
intended CRITICAL line followed by uvicorn's own ~20-line lifespan
`SystemExit` traceback. Failing here means the process exits on the first
line alone, which is what a `kubectl logs` on a CrashLoopBackOff should show.

This is also the one place `Settings.pokeproxy_port` gets read; nothing else
does.
"""

from __future__ import annotations

import uvicorn

from pokeproxy.main import _load_settings


def main() -> None:
    settings = _load_settings()
    uvicorn.run(
        "pokeproxy.main:app",
        host="0.0.0.0",  # noqa: S104 — the container network boundary is the pod, not this bind
        port=settings.pokeproxy_port,
        log_config=None,  # pokeproxy.main already installed the JSON logging config on import
    )


if __name__ == "__main__":
    main()
