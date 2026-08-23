# L6 — Mock downstream service was never a deployable artifact

**Severity:** Low · **Part:** 2, step 3 · **Status:** Fixed
**Files:** `app/pyproject.toml`, `app/mock_service/main.py`, `app/Dockerfile.mock`

## Problem

`mock_service` was a plain module directory, not a package: `[tool.hatch.build.targets.wheel] packages = ["src/pokeproxy"]` never included it, and its only entrypoint was `if __name__ == "__main__": uvicorn.run(app, host="127.0.0.1", ...)`, which only works when invoked as `python mock_service/main.py` from exactly `app/` with the source tree present on disk. There was no path from "this test double exists" to "this test double runs anywhere but a developer's own checkout."

## Production Impact

None directly in Part 1 — it's a test double, not production code. The real cost was structural: Part 2 needs mock-downstream running as its own container so PokeProxy has something real to forward to inside the cluster, and Part 3's E2E gate needs it running the same way in CI. Without a containerization decision, that need would have been solved ad hoc, probably by smuggling `mock_service` into the production image — which is a worse outcome (a test double shipping inside the artifact that goes to production) than the one deliberately chosen here.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Where mock_service lives at runtime | packaged into the same wheel/image as `pokeproxy` · a separate image | **separate image** |
| How it becomes importable without wheel packaging | add it to `[tool.hatch.build.targets.wheel] packages` · `PYTHONPATH` in the container | **`PYTHONPATH=/app`** |
| Dependencies | reuse `app/uv.lock` (pulls in `httpx`, `protobuf`, `redis`, `pydantic-settings`) · pin only what the mock actually needs | **pin only `fastapi`/`uvicorn`, matching the exact versions already resolved in `uv.lock`** |

## Decision

A dedicated `Dockerfile.mock`, deliberately not sharing `pokeproxy`'s dependency lockfile. The production image should contain only what runs in production — bundling a test double into it blurs that line for no benefit. `PYTHONPATH=/app` is the actual containerization decision L6 asked for: it makes `mock_service.main:app` importable to uvicorn without ever making it an installable package, since there's no real need for `mock_service` to be a wheel — it only ever runs as `uvicorn mock_service.main:app` inside this one container.

Pinning `fastapi==0.135.1`/`uvicorn[standard]==0.41.0` (the exact versions `app/uv.lock` already resolved) rather than `uv sync`-ing the shared lockfile keeps the mock image free of `httpx`/`protobuf`/`redis`/`pydantic-settings` — none of which it uses — while guaranteeing the two images can't silently drift apart on framework version without that being a deliberate, visible bump.

## Implementation

`app/Dockerfile.mock`: multi-stage, `uv venv` + `uv pip install` for the two pinned packages (not `uv sync`), non-root `uid=10001`, `readOnlyRootFilesystem`, `CMD ["uvicorn", "mock_service.main:app", "--host", "0.0.0.0", "--port", "8001"]`.

`mock_service/main.py` gained `GET /health` — plain liveness, no dependencies to gate on — and lost the `if __name__ == "__main__":` block entirely: the documented run path is always the `uvicorn` CLI (both the local `app/README.md` Quick Start and now `Dockerfile.mock`'s own `CMD`), so the block was dead code, and its `127.0.0.1` bind was literally the evidence cited for H6 (issue 013). Per the standing code-style rule (`CLAUDE.md`), code superseded by the current change is removed as part of it rather than left behind.

## Verification

| Check | Result |
|---|---|
| Build | 16.8s cold, image **236 MB** |
| Runtime user | `uid=10001(mockdownstream)` |
| `--read-only --cap-drop ALL --security-opt no-new-privileges` | Serves normally; `docker diff` shows **zero filesystem writes** |
| Bind address | `Uvicorn running on http://0.0.0.0:8001`, confirmed reachable via a published port, not just loopback |
| `GET /health` | `{"status":"alive"}`, 200 |
| `POST /pokemon` → `GET /received` round-trip | Body and `X-Grd-Reason` header both land correctly |
| Full app test suite | Unaffected — `mock_service` was and remains outside pytest coverage; a test double testing a test double is circular |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| `mock_service` still isn't a wheel-packaged Python module | Deliberate — it only ever needs to run as one specific `uvicorn` invocation inside one specific container; packaging it would be ceremony with no consumer |
| Framework versions are pinned by hand rather than derived from a shared lockfile | Accepted cost of keeping the image dependency-minimal; a version bump now requires updating two places instead of one, which is a fair trade against not shipping `redis`/`protobuf` into a container that has no use for them |
