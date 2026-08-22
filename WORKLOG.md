# PokeProxy Engineering Work Log

This document is the persistent engineering state for the assignment. I update it as decisions are actually made rather than trying to design the entire solution upfront.

**Standing rule — token economy.** Maximum information in minimum cost, in every response, document and tool call. Tables over prose; lead with the answer. Never compress evidence, exact numbers, `file:line` refs, honest uncertainty, or the reasoning behind a decision — compress the packaging. Terse is the goal; vague is a failure. Defined in `CLAUDE.md`, applies to this file and everything under `docs/`.

## Current State

**Current phase:** Part 1 — Wave 3 done, Wave 4 in progress. C1, C5, C2, C3, C4, H1, M1+H7 fixed.

**Completed:**
- Read-only review of `app/`: source, config, tests, mock service, load generator.
- Prioritized Part 1 issue inventory (below).
- `docs/planning/part-01-production-hardening.md`.
- **Wave 0 (L7) — toolchain works.** `app/.venv` (Python 3.13) plus `uv 0.12.5`, both **inside WSL Ubuntu**, not on Windows. All verification runs via `wsl.exe`. Windows Python is still 3.11 and `uv` is not on the Windows PATH.
- **Wave 1 (C1) — HMAC key name and validation.** Write-up: `docs/issues/001-hmac-key-configuration.md`.
- **Wave 2 (C5) — structured JSON logging, `X-Request-ID` correlation, outcome seam.** Write-up: `docs/issues/002-structured-logging.md`.
- **Wave 3, part 1 (C2) — bounded forward retry, shared client, `.env`-configurable cap/deadline.** Write-up: `docs/issues/003-unbounded-forward-retry.md`.
- **Wave 3, part 2 (C3) — cache lookup is a single `GET`, not a keyspace scan.** Write-up: `docs/issues/004-cache-keyspace-scan.md`.
- **Wave 3, part 3 (C4) — Redis calls guarded, client-level timeouts.** Write-up: `docs/issues/005-unguarded-redis-calls.md`.
- **Wave 3, part 4 (H1) — rules loaded and validated once at startup, not per request.** Write-up: `docs/issues/006-rules-reloaded-per-request.md`.
- **Wave 4, part 1 (M1 + H7) — real `/ready` endpoint split from `/health`, flipped false at the start of shutdown.** Write-up: `docs/issues/007-liveness-readiness-split.md`.

**Currently working on:**
- Nothing in flight. M1+H7 is complete and verified; awaiting go-ahead for the next Wave 4 pair (H2 + H3).

**Repository state:** branch `feature/repo-review` tracking `origin`. Commits through H1 pushed (`5d37e7e`). M1+H7 uncommitted.

**Next:**
1. Wave 4: H2 + H3 (header hygiene), then H4 + H5 (stats accounting).

**C4 closed both deferred test-isolation reasons from C5.** `no_cache` in `test_logging.py` is no longer load-bearing for correctness (a Redis-down request now degrades instead of 500ing) — kept anyway for test speed and to keep unit tests off real network calls. Confirmed by a real end-to-end run with no mocking: unreachable Redis produced two `WARNING` log lines and a clean `502 downstream_error` in 727.8ms, not a crash.

**Verified baselines** (measured in WSL, not assumed):
- Test suite: **67 passed** from `app/` (5 → 16 after C1 → 28 after C5 → 38 after C2 → 44 after C3 → 48 after C4 → 56 after the C2/C4 config-naming follow-up → 62 after H1 → 67 after M1+H7).
- Tests are CWD-dependent — from the repo root, 3 fail with `FileNotFoundError: 'config/rules.json'`. Confirms M7; fix it there.
- `ruff check .` passes across the project today, so L5 is a *missing gate*, not a backlog of violations.
- `aioredis.from_url` is **lazy**: the app starts fine with nothing listening on 6379. Input to C4 and M1.
- App module import alone costs ~3.2 s over the WSL `/mnt/c` filesystem. Re-measure startup timing inside the container in Part 2 rather than trusting numbers taken here.

**Important decisions so far:**
- Environment variables are the configuration interface. `.env` stays a local-dev convenience and is never the production mechanism.
- Redis is a best-effort cache and must **not** gate readiness. A Redis outage should cost latency, not availability.
- Fix the outcome-accounting *seam* in Part 1; defer the Prometheus backend to Part 4. Instrument once, not twice.
- Structured logging lands before the behavioural bug fixes, not after. Reasoning is in the planning doc.
- Standardize on `POKEPROXY_HMAC_KEY` — align the docs to the code rather than the reverse.
- **Minimum decoded HMAC key length is 16 bytes (128 bits), not 32.** This keeps C1 scoped to configuration: the existing 25-byte dev secret still passes, so `.env.example` and `scripts/load_generator.py` keep their current values and C1 does not spill into the load generator. L1 (a working secret committed to the repo) stays a standalone Wave 5 item.
- **M4 — a cache hit skips the downstream forward.** The cache becomes a deduplication / idempotency layer rather than a protobuf-decode cache, which is what "avoid re-processing previously seen payloads" actually implies and what makes the Redis dependency earn its place. Consequences are recorded under the M4 open sub-questions below and in the planning doc.
- **H1 — rules are loaded and validated once at startup.** No per-request disk read, no hot-reload. Invalid config is a startup failure, not a request-time 500. A rules change is a pod restart, which is an honest rollout story for Part 3.
- Order of work: make it start, make it visible, make it correct, make it survive Kubernetes, then hygiene.

**Open questions:**
Sub-questions opened by the M4 decision. All need answers before Wave 3 touches `cache.py`; none of them reopen the decision itself.

- **What does a suppressed duplicate return to the client?** The cache stores the decoded `PokemonJSON`, not the downstream response, so there is no prior result to replay. Either return a deterministic synthetic response (a 200 with an explicit duplicate marker, or 208 Already Reported), or start caching the downstream response as well and replay it. The first is simpler and does not pretend to know what downstream would say this time.
- **Should the rules config hash be part of the cache key?** Under H1 a rules change is a pod restart — but a payload already inside the dedup window is suppressed *before* routing happens, so the new rules never see it for up to one TTL. Mixing the config hash into the key makes a rules change invalidate dedup automatically. Clean, at the cost of a downstream re-delivery burst on every rules change.
- **Is 300s the right dedup window, and should it be configurable?** `CACHE_TTL` is hardcoded at `cache.py:11`. Under M4 it stops being an implementation detail and becomes the business-visible dedup window, which argues for making it configuration.

Still open from before:

- **M2 — where the body size limit belongs.** App-level streaming enforcement, or push it to ingress/uvicorn and keep a defence-in-depth check in the app. Decide in Wave 5.

## Backlog / Later

Items discovered during the Part 1 review that intentionally belong to a later Part.

**Part 2 — Infrastructure & Deployment**
- Downstream URLs in `config/rules.json` are all `http://localhost:8001/pokemon`. These become per-environment ConfigMap values.
- **H1 consequence:** rules are read once at startup, so a rules ConfigMap change does nothing until the pods restart. The Deployment needs a pod-template checksum annotation over the rules ConfigMap (or an operator like Reloader) so a rules change actually rolls. Without it, a rules update looks applied in git and is silently inert in the cluster.
- `mock_service` is not in the wheel packaging (`packages = ["src/pokeproxy"]`) and binds `127.0.0.1`. Needs a deliberate containerization decision (L6).
- `preStop` hook and `terminationGracePeriodSeconds` are the manifest half of graceful shutdown (H7). The app-side hooks land in Part 1.
- Probe configuration encodes the "Redis does not gate readiness" decision (M1).

**Part 3 — CI/CD & GitOps**
- `mock_service` keeps received payloads in an in-process list. If it ever runs more than one replica, the E2E check (post through the proxy, then read `/received`) can hit a different pod and see nothing. Pin it to a single replica or the E2E is flaky.
- `scripts/load_generator.py` is the natural seed for the E2E traffic generator. Its `sys.path.insert` hack and hardcoded default secret should be cleaned so CI can import it.
- **M4 consequence — the load generator stops generating load.** It picks from 12 fixed payloads (`POKEMON_DATA`, `random.choice` at `load_generator.py:92`), and protobuf serialization of identical field values is byte-identical, so there are exactly 12 distinct body hashes. Once dedup skips the forward, the first dozen requests exercise the downstream path and everything else for the next TTL is suppressed. A 60s run at 10 rps would forward ~12 of 600 requests. The generator needs a varying field (nonce or timestamp) to stay useful for load testing, and Part 4 dashboards will read as near-zero forward rate until it has one.
- **M4 consequence — the post-deploy E2E must use a unique payload per run**, or flush the dedup key first. Re-running the same E2E payload inside the TTL produces no new downstream delivery, so the check fails on a healthy deployment. This is a correctness requirement for the Part 3 gate, not a nicety.
- Tests use relative fixture paths (`load_rules("config/rules.json")`) and only pass when CWD is `app/`. Fixed as part of M7 so CI is not CWD-dependent.

**Part 4 — Observability**
- Replace `/stats` with Prometheus instrumentation, reusing the Part 1 outcome-accounting seam (H4, H5).
- **M4 consequence:** "duplicate suppressed" must be its own terminal outcome in the seam. Otherwise suppressed traffic is invisible — the same failure class as no-rule-matched today, where a request disappears and the service looks healthy. It also needs to be distinguishable on the dashboard from a genuine drop in inbound traffic.
- `StatsRegistry` keys on downstream URL via `setdefault` — bounded by the rules file today, but it is an unbounded-cardinality pattern that must not be carried into Prometheus labels (L4).
- Move operational endpoints (`/stats`, `/metrics`) onto a port the public Service does not expose (M5).
- If H1 ends up with hot-reload, expose a config hash so I can tell which pods have picked up a rules change.

**Documented, not implemented**
- M3 — no replay protection on signed payloads. The HMAC covers the body only, so a captured request is valid forever. The fix (signed timestamp inside the HMAC input, bounded acceptance window, nonce cache in Redis) is a protocol change I cannot make unilaterally. Write it up as a known gap.

---

## Part 1 — Code Review & Production Hardening

### Initial assessment

Three structural observations, ahead of any individual bug:

1. **It cannot start from its own documentation.** The code requires `POKEPROXY_HMAC_KEY`; the README and `.env.example` both say `POKEPROXY_SECRET`. Nobody has run this from a clean checkout recently. *(Fixed in C1 — the service now starts from `.env.example` verbatim.)*
2. **It is completely dark.** No `logging` import anywhere in `app/`. The only introspection is `/stats`, and `/stats` is wrong in precisely the way that hides an outage.
3. **The failure handling is inverted.** The dependency that should degrade gracefully (Redis) is a hard failure; the dependency that should fail fast (a dead downstream) retries forever. That is backwards, and it is the difference between a bad minute and a bad night.

### Prioritized backlog

Wave numbering matches the order of work in `docs/planning/part-01-production-hardening.md`.

| ID | Sev | Issue | Primary evidence | Wave | Status |
|----|-----|-------|------------------|------|--------|
| L7 | — | No `.venv`, `uv` not on PATH, local Python is 3.11 vs required 3.13. Nothing verified by execution yet. | environment | 0 | **Resolved** — venv + `uv 0.12.5` exist, but in **WSL**, not Windows. Verification runs via `wsl.exe`. |
| C1 | Critical | HMAC secret var name matches no docs, and the value has no meaningful validation. `changeme` (6 bytes), `""` (0 bytes) and `abcd efgh` (whitespace silently discarded) all started successfully. A base64 *padding* error did fail at startup, accidentally, as a bare `binascii.Error`. | `config.py:18,23-25`, `.env.example:1`, `README.md:52` | 1 | **Fixed** — `docs/issues/001-hmac-key-configuration.md` |
| C5 | Critical | No logging of any kind. Every failure path returns JSON and vanishes. No request or correlation ID. Measured: bad vs **missing** signature logged identically; a Redis-down 500 produced ~100 raw traceback lines. | no `logging` import in `app/` | 2 | **Fixed** — `docs/issues/002-structured-logging.md`. Included the C1 leftover (clean config-failure line). |
| C2 | Critical | `while True` retry with no cap and no deadline, a new `AsyncClient` per attempt never closed, `timeout=600.0` overriding the configured timeouts, and `app.state.http_client` left as dead code. Measured: 4 leaked clients in 3s against a refused connection, still retrying. | `proxy.py:54-66`, `main.py:24-27` | 3 | **Fixed** — `docs/issues/003-unbounded-forward-retry.md`. Cap and deadline are `.env`-configurable (`FORWARD_MAX_ATTEMPTS`, `FORWARD_DEADLINE_SECONDS` — corrected from an earlier, wrongly-documented `POKEPROXY_` prefix, see below). |
| C3 | Critical | `redis.keys("pokeproxy:pokemon:*")` on every request, then a Python-side linear scan to find the key a single `GET` would have returned. | `cache.py:18` | 3 | **Fixed** — `docs/issues/004-cache-keyspace-scan.md`. |
| C4 | Critical | Redis calls unguarded, no socket or connect timeouts. A Redis blip becomes a 500 on 100% of traffic; a hung Redis blocks indefinitely. Both halves reproduced: connection-refused and a raw TCP server that accepts and never responds. | `proxy.py:142,153`, `main.py:29` | 3 | **Fixed** — `docs/issues/005-unguarded-redis-calls.md`. |
| H1 | High | `rules.json` re-read, re-parsed and re-validated from disk on every request, synchronously, on the event loop. Config errors surface at request time instead of startup. | `proxy.py:207` (pre-fix), `rules.py:110-135` | 3 | **Fixed** — `docs/issues/006-rules-reloaded-per-request.md` |
| M1 | Medium | `/health` is a hardcoded string. No readiness concept. | `main.py:167-169` (pre-fix) | 4 | **Fixed** — `docs/issues/007-liveness-readiness-split.md` |
| H7 | High | No graceful shutdown and no readiness flip on SIGTERM. Every rollout drops in-flight requests. | `main.py:60-98` (pre-fix) | 4 | **Fixed (app-side half)** — `docs/issues/007-liveness-readiness-split.md`. K8s-side `preStop`/grace-period wiring is Part 2 |
| H2 | High | Downstream response headers copied verbatim to the client, including framing and hop-by-hop headers. | `proxy.py:89-93` | 4 | Open |
| H3 | High | Client headers forwarded downstream on a denylist basis. Denylists are always incomplete. | `proxy.py:41-51,22-33` | 4 | Open |
| H4 | High | `request_count` only increments on success while `error_count` increments on failure, so `error_rate` reads 0.0 during a total outage. `bytes_received` is assigned rather than accumulated. Rejections and no-rule-matched are never counted. | `stats.py:29`, `proxy.py:87,95,101,162` | 4 | Open |
| H5 | High | `_response_times` grows without bound and `bisect.insort` is O(n) per insert, so memory and CPU degrade with uptime. | `stats.py:15,19` | 4 | Open |
| H6 | High | Config assumes localhost, relative paths and a loopback bind. None of it survives a container. | `config/rules.json`, `config.py:15`, `mock_service/main.py:34` | 4 / P2 | Open |
| M2 | Medium | ~~`int(content_length)` unguarded, so a malformed header is a 500.~~ **First half disproved:** measured during C5 — uvicorn's httptools parser rejects `Content-Length: abc` with its own 400 before the handler runs, so `int()` never sees a non-digit string. Second half stands: body is fully buffered *before* the size check, so the 1 MiB limit does not actually limit anything. | `proxy.py:114-126` | 5 | Open — re-derive before fixing |
| M6 | Medium | `POKEPROXY_PORT` is defined and documented but read by nothing. The real port comes from the uvicorn CLI. | `config.py:20`, `.env.example:3` | 5 | Open |
| M7 | Medium | Five tests, all on decode/parse/match. Nothing covers `/stream`, HMAC, cache, Redis failure, downstream failure, headers or size limits. | `tests/test_basic.py` | 5 | Open |
| L1 | Low | A working HMAC secret is committed in `.env.example` and hardcoded as the load generator default. | `.env.example:1`, `load_generator.py:74` | 5 | Open |
| L2 | Low | Empty-name payloads rejected as "likely garbage input" — a heuristic wearing validation's clothes, with a misleading error message. | `config.py:83-84` | 5 | Open |
| L3 | Low | Error responses are opaque and uncorrelatable. `{"error": "downstream error"}` gives support nothing to search on. | `proxy.py:96-105,134-137,149-152` | 5 | Open |
| L5 | Low | `ruff` is configured with a good ruleset and nothing runs it. No type gate despite `# type: ignore` throughout. | `pyproject.toml` | 5 | Open |
| M4 | Medium | Cache costs a Redis round trip to save a microsecond-scale protobuf decode, and a hit still forwards downstream anyway. | `cache.py`, `proxy.py:161-167` | 3 | Open — dedup semantics decided |
| M3 | Medium | No replay protection. The HMAC covers the body only. | `proxy.py:36-38` | — | Deferred, document only |
| M5 | Medium | `/stats` is unauthenticated and leaks internal downstream URLs. | `main.py:47-50` | — | Deferred to Part 4 |
| L4 | Low | `setdefault` keyed by URL — an unbounded-cardinality pattern. | `stats.py:53` | — | Deferred to Part 4 |
| L6 | Low | `mock_service` is not in the wheel packaging and imports as a top-level module. | `pyproject.toml` | — | Deferred to Part 2 |

### Decisions and changes

**C1 — HMAC key configuration (Wave 1).** Standardized on `POKEPROXY_HMAC_KEY` and aligned `.env.example` and the README to it; rejected `AliasChoices` because there is nothing to migrate. Added a `field_validator` on `Settings` enforcing strict base64 and a 16-byte decoded minimum, with an error naming the variable and giving the `openssl` command. `hmac_key` and the validator share one `_decode_hmac_key()` helper.

Verified: 11 new tests (suite 5 → 16); the new module run against HEAD's code fails 9 of 11, confirming real regression cover; the documented Quick Start now reaches `Application startup complete` where it previously exited 3; `POKEPROXY_HMAC_KEY=changeme` exits 3 with the actionable message; `ruff check .` clean.

Deliberately out of scope: `scripts/load_generator.py` is untouched (its 25-byte default still passes the 16-byte floor — that was the reason for choosing 16). Full reasoning and residual risk in `docs/issues/001-hmac-key-configuration.md`.

**C2 — bounded forward retry (Wave 3).** `_forward_with_retry` now reuses the already-existing, already-correctly-configured `app.state.http_client` instead of leaking a new `AsyncClient` per attempt, and stops after a configurable `FORWARD_MAX_ATTEMPTS` (default 3) or `FORWARD_DEADLINE_SECONDS` (default 10.0), whichever comes first — both validated at startup like C1. Exhaustion re-raises the original exception type, so the existing `downstream_timeout`/`downstream_error` outcome handling from C5 applies with no new outcome value. `RetryPolicy` (a frozen dataclass) and a pure `_next_backoff_delay` function replace the old ad-hoc loop state.

The retry loop takes its clock and sleep function as injected dependencies (`clock: Callable[[], float] = time.monotonic`, `sleep: ... = asyncio.sleep`) rather than calling them directly — this came out of debugging a flaky deadline test that monkeypatched the global `time.monotonic` and starved asyncio's own internal scheduler, which reads the same function.

Verified: 10 new tests (28 → 38) — 7 isolated retry-loop tests plus the 3 `forwarded`/`downstream_timeout`/`downstream_error` outcome tests C5 had deferred, now unblocked; manual end-to-end check (downstream mocked, cache bypassed since Redis wasn't guarded yet) showed a bounded 502 in ~277ms where the old code would have retried forever; `ruff check .` clean. Full detail in `docs/issues/003-unbounded-forward-retry.md`.

**C3 — cache lookup is a single GET (Wave 3).** `get_cached_pokemon` replaced `KEYS "pokeproxy:pokemon:*"` plus a Python-side scan with one `redis.get(cache_key)` — same key format, same TTL, same return shape, no behavior change beyond dropping the O(keyspace) cost. No live Redis in this environment, so verification uses a minimal in-memory fake implementing the `get`/`set`/`keys` subset actually called; the regression test asserting `keys_calls == 0` fails against HEAD's code (`1 == 0`) and passes against the fix. 6 new tests (38 → 44); `ruff check .` clean. Full detail in `docs/issues/004-cache-keyspace-scan.md`.

**C4 — guarded Redis calls, client-level timeouts (Wave 3).** `get_cached_pokemon`/`cache_pokemon` each wrap their Redis call in `try`/`except RedisError`, logging a `WARNING` with the traceback and degrading to a miss / a no-op write instead of propagating. `aioredis.from_url` gains `socket_connect_timeout=2.0` and `socket_timeout=2.0`, so a hung (not just unreachable) Redis is also bounded. Guard lives inside `cache.py`, not at the `proxy.py` call sites — best-effort is a property of the cache abstraction, so callers shouldn't need to know Redis can fail.

Verified two distinct failure modes for real, not just against a fake: a refused connection (full request through `TestClient`, no mocking — two `WARNING` lines, then a clean `502` in 727.8ms) and a genuinely hung Redis (a raw TCP server that accepts and never responds — `TimeoutError` raised at the 2.0s socket timeout, caught, logged, process exits cleanly in 2.88s, not a hang). 4 new unit tests (44 → 48); the same 4 run against pre-fix `cache.py` all fail with an uncaught `ConnectionError`. `ruff check .` clean. Full detail in `docs/issues/005-unguarded-redis-calls.md`.

**Follow-up — Redis timeouts made `.env`-configurable, and a real naming bug fixed.** `REDIS_CONNECT_TIMEOUT_SECONDS`/`REDIS_SOCKET_TIMEOUT_SECONDS` (both default 2.0) replace the two hardcoded constants from the original C4 fix. While extending the pattern, found that C2's `.env.example`/`README.md`/validator messages documented `POKEPROXY_FORWARD_MAX_ATTEMPTS`/`POKEPROXY_FORWARD_DEADLINE_SECONDS` — but `Settings` has no `POKEPROXY_` prefix, so the *actual* working names were always `FORWARD_MAX_ATTEMPTS`/`FORWARD_DEADLINE_SECONDS` (matching `REDIS_URL`, `LOG_LEVEL`). The field mapping itself was never broken; only the docs and error-message text named a variable the process silently ignored. Confirmed the ignored-var behavior with `FORWARD_MAX_ATTEMPTS=9` under the wrong prefix (stayed at default 3) vs. the correct name (took effect). Also caught two tests in `test_logging.py` that set `POKEPROXY_FORWARD_MAX_ATTEMPTS=1` expecting a fast single-attempt run — silently ignored, so those tests were passing by coincidence (3 attempts still fit inside the test's own timing budget), not for the reason claimed. Fixed both.

All four operational settings (`forward_max_attempts`, `forward_deadline_seconds`, `redis_connect_timeout_seconds`, `redis_socket_timeout_seconds`) now share one `_check_positive_seconds` validator keyed off `pydantic.ValidationInfo.field_name`, so the error message always names the variable the process actually reads. 8 new tests in `test_config.py` (48 → 56) proving each of the four env vars actually takes effect and that non-positive values are rejected by name — this is the test category that was missing and let the original naming bug ship unnoticed. `.env.example`/`README.md`/both issue docs corrected. Not committed — done as a direct follow-up request, no new `docs/issues/` entry opened for it since it's a correction to already-documented C2/C4, not a new Part 1 issue.

**H1 — rules loaded once at startup (Wave 3).** `main.py` gains `_load_rules(config_path)`, mirroring `_load_settings`: calls `load_rules`, and on `OSError`/`ValueError` (missing file, malformed JSON, or any of `load_rules`'s own validation errors) logs one `CRITICAL` line and `raise SystemExit(1)` instead of letting the app start with a config it can't use. `lifespan()` calls it once and stores the result on `app.state.rules`, replacing `app.state.config_path`. `proxy.py` no longer imports or calls `load_rules` — `stream()` reads `request.app.state.rules` directly.

Verified: 6 new tests (`test_startup.py`, 56 → 62) — `app.state.rules` populated at startup; `load_rules` patched and counted across 3 requests through `TestClient`, confirming exactly one call; invalid rule / malformed JSON / missing file each raise `SystemExit`; the failure logs at `CRITICAL` naming the reason. `ruff check .` clean. Full detail in `docs/issues/006-rules-reloaded-per-request.md`.

**M1 + H7 — liveness/readiness split (Wave 4).** `/health` is unchanged — cheap, always trivially true. New `GET /ready` reads `app.state.ready`, a bool set `True` at the end of `lifespan`'s startup section and `False` as the first line after `yield` resumes, before the Redis/HTTP client cleanup runs. Returns 503 when false. Deliberately does not probe Redis — a Redis blip must not un-ready every pod at once, matching the readiness decision already recorded in `docs/planning/part-01-production-hardening.md`. `/ready` joins `/health`/`/stats` in `_UNLOGGED_PATHS`. Naming considered and rejected the `/healthz`+`/readyz` convention in favor of keeping the existing documented `/health` and adding `/ready` — smaller diff, no rename risk.

This is the app-side half only. The K8s-side `preStop`/`terminationGracePeriodSeconds` wiring that gives the flip time to actually stop traffic before `SIGTERM` is Part 2 (Backlog).

Verified: 5 new tests (`test_readiness.py` ×4, one in `test_logging.py`; 62 → 67) — `/ready` is 200 after startup, 503 when forced not-ready, `app.state.ready` is false after the app's lifespan shutdown runs, `/health` is unaffected by readiness state, `/ready` produces no access line. `ruff check .` clean. Full detail in `docs/issues/007-liveness-readiness-split.md`.

**C5 — structured logging (Wave 2).** stdlib `logging` plus a JSON formatter in a new `logging_config.py`; no new dependency, and one setup unifies uvicorn's own output instead of leaving it plaintext alongside ours. Uvicorn's access log is replaced by our own middleware line because it cannot carry a correlation ID, latency or outcome. Correlation via `X-Request-ID` — deliberately the infrastructure convention rather than an `X-Grd-*` name, so the trace survives the ingress. Handlers label `request.state.outcome`; the middleware is the single place that reads it and emits one access line. Unhandled exceptions become a JSON 500 carrying the request ID rather than a raw ASGI traceback. Also folded in the C1 leftover: bad config is now one CRITICAL line.

Kept deliberately simple — no `contextvars`, no outcome enum, no console log format.

Verified: 12 new tests (16 → 28); the same 5-case probe used for the "before" measurement went from **116 plaintext lines to 15 structured JSON records** (plus 88 traceback lines, kept multi-line on purpose); the two 401s are now distinguishable; `ruff check .` clean. Full detail in `docs/issues/002-structured-logging.md`.

---

## Part 2 — Infrastructure & Deployment

_Not started._

---

## Part 3 — CI/CD & GitOps

_Not started._

---

## Part 4 — Observability

_Not started._

---

## Part 5 — Zero-to-Running Automation

_Not started._

---

## Final Review / Remaining Work

_Not started._
