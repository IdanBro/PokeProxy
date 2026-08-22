# PokeProxy Engineering Work Log

This document is the persistent engineering state for the assignment. I update it as decisions are actually made rather than trying to design the entire solution upfront.

**Standing rule — token economy.** Maximum information in minimum cost, in every response, document and tool call. Tables over prose; lead with the answer. Never compress evidence, exact numbers, `file:line` refs, honest uncertainty, or the reasoning behind a decision — compress the packaging. Terse is the goal; vague is a failure. Defined in `CLAUDE.md`, applies to this file and everything under `docs/`.

## Current State

**Current phase:** Part 1 — every audit SHOULD FIX item is now closed (fixed or deliberately deferred). C1, C5, C2, C3, C4, H1, M1+H7, H2+H3, H4+H5, M4, M7-CWD fixed. **L3 and M2 reviewed and deliberately deferred** (below) rather than fixed. Remaining: one explicit re-deferral pass over the NICE TO HAVE backlog (L1, L2, L5, M6, H6), then Part 1 is done.

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
- **Wave 4, part 2 (H2 + H3) — header hygiene in both directions.** Request headers to downstream now go through an allowlist (currently empty); response headers to the client have hop-by-hop headers stripped. Write-up: `docs/issues/008-header-hygiene.md`.
- **Wave 4, part 3 (H4 + H5) — outcome accounting fixed, unbounded response-time storage removed.** `EndpointStats.record_request(is_error)` makes request/error counting atomic, so `error_rate` can no longer read 0.0 during a total outage. Rejections and `no_rule_matched` now count via a new outcome-keyed `StatsRegistry.record_outcome`. `_response_times`/`percentile()` deleted entirely (not bounded) — percentiles are Part 4's job (Prometheus histograms), `/stats` keeps only `avg_response_time` (already O(1) memory). Write-up: `docs/issues/009-outcome-accounting-and-unbounded-stats.md`.
- **Wave 3, part 5 (M4) — cache becomes a real dedup layer.** A hit now replays the actual cached downstream response (status, filtered headers, content) and skips decode/routing/forward entirely — transparent to the client. Only `forwarded` outcomes get cached, never `downstream_timeout`/`downstream_error`. New `duplicate_suppressed` outcome, `CACHE_TTL_SECONDS` now `.env`-configurable. Write-up: `docs/issues/010-cache-becomes-a-dedup-layer.md`.
- **Wave 5 (M7-CWD) — test suite no longer depends on the invoking shell's working directory.** New `app/tests/conftest.py` pins CWD to `app/` via `pytest_configure()`, computed from the test files' own location. Verified from three different working directories (`app/`, repo root, `/tmp`) — 94/94 every time. Write-up: `docs/issues/011-test-suite-cwd-dependence.md`.

**Currently working on:**
- Nothing in flight. M7-CWD complete and verified — every audit SHOULD FIX item is now either fixed (H4+H5, M4, M7-CWD) or deliberately deferred with reasoning (L3, M2). Only the final NICE TO HAVE re-deferral pass (L1, L2, L5, M6, H6) stands between here and declaring Part 1 done.

**Repository state:** branch `feature/repo-review` tracking `origin`. Commits through M4 pushed (`a9eaf9a`). M7-CWD uncommitted.

**L3 — deliberately deferred, not missed (decided 2026-08-22).** Error responses (`{"error": "downstream error"}`, etc.) carry no `request_id`, unlike `main.py`'s own `internal_error` handler, which does — inconsistent, and exactly the "useful error messages" gap the assignment names. Root cause and fix were fully scoped in a pre-change review: inject `request.state.request_id` into the content dict at the 6 real error call sites already funneled through `proxy.py`'s `_outcome_response()` helper (built during H4) plus the 2 `JSONResponse` literals in `_forward_request`'s except blocks; `no_rule_matched`'s `{}` body stays untouched since it isn't an error. A related, separately-decidable question was also raised and left open: `rejected_signature_missing` and `rejected_signature_invalid` currently return identical body text (`"invalid signature"`) despite being distinguishable outcomes in the logs.

**Why L3 deferred rather than fixed:** low severity (originally classified **Low**, the only Low-severity item in the SHOULD FIX set — H4/H5 were High, M2/M4/M7-CWD are Medium and each guard a real correctness/CI risk). The `X-Request-ID` response *header* already carries the correlation ID today, so this is a body-vs-header convenience gap, not a hard blocker. **Not forgotten — scoped and ready to implement in one pass through `proxy.py` whenever it's picked back up.**

**M2 — deliberately deferred, not missed (decided 2026-08-22).** `int(content_length)` malformed-header half was already disproved during C5 (uvicorn's own parser rejects it with a 400 before the handler runs). The real, still-live half: `await request.body()` fully buffers the request into memory *before* the `len(body) > MAX_BODY_SIZE` check ever runs — a client that omits or lies about `Content-Length` gets its entire payload buffered regardless of size, which is a genuine resource-exhaustion vector, not just a cosmetic gap. Fix was fully scoped in a pre-change review: read the body incrementally via `request.stream()`, counting bytes and aborting with 413 the moment the running total crosses `MAX_BODY_SIZE`, instead of reading to completion first. The existing `Content-Length` pre-check stays as a cheap first-pass shortcut for honest clients.

**Why M2 deferred rather than fixed:** user's explicit call, consistent with the H5 percentile-removal precedent — this class of protection is also achievable at the ingress/reverse-proxy layer in Part 2 (e.g., an Ingress `client_max_body_size`-equivalent), which is the more standard place production systems put it and rejects before the request even reaches this process. Recorded as a Part 2 addition below (defense-in-depth, not a replacement for the app-level fix, which stays scoped and ready if picked back up first).

**Next:**
1. Explicitly re-defer every remaining NICE TO HAVE (L1, L2, L5, M6, H6) with reasoning — same treatment L3/M2 already got — then declare Part 1 done and move to Part 2.

**Part 1 completion audit (2026-08-22):** Full requirement-by-requirement pass against `README_HOME_ASSIGNMENT.md` Part 1 and this doc's own "Definition of done" (`docs/planning/part-01-production-hardening.md`). Verification run: `ruff check .` clean, `pytest -q` from `app/` — **73 passed**; from repo root — 25 fail (CWD-dependence, see Verified baselines).

*Satisfied:* reliability fixes for every issue actually fixed (C1-C5, H1-H3) each with a regression test and a `docs/issues/` write-up; structured logging; configuration hygiene for the HMAC key specifically; graceful shutdown (app-side, K8s-side correctly scoped to Part 2).

*Fixed since the audit:*
- **The outcome-accounting seam** — H4+H5 fixed 2026-08-22. See Decisions and changes below and `docs/issues/009-outcome-accounting-and-unbounded-stats.md`.
- **The dedup decision** — M4 fixed 2026-08-22, response-caching design (user's call, transparent to the client) rather than the synthetic-marker design originally proposed. See Decisions and changes below and `docs/issues/010-cache-becomes-a-dedup-layer.md`.
- **M7's CWD-dependence** — fixed 2026-08-22, `conftest.py`. See Decisions and changes below and `docs/issues/011-test-suite-cwd-dependence.md`.

*Not yet satisfied, despite being named or self-committed:* none remaining — every SHOULD FIX item is now either fixed or deliberately deferred with reasoning (see below).

*Deliberately deferred, with reasoning already on record — no gap:* K8s-side of graceful shutdown (H7, Backlog/Part 2), rules ConfigMap live-reload (H1 consequence, Backlog/Part 2), H6 config-assumes-localhost (fundamentally a Part 2 deployment-topology decision), M3 replay protection (documented-only, protocol change), M5 `/stats` auth (Backlog/Part 4), L4 unbounded label cardinality (Backlog/Part 4), L6 `mock_service` packaging (Backlog/Part 2), L5 ruff-in-CI (natural Part 3 fit), **L3 useful error messages** (reviewed, root-caused, fix scoped, deprioritized below Medium-severity work — see "L3 — deliberately deferred, not missed"), **M2 body size limit doesn't actually limit** (reviewed, root-caused, fix scoped, also achievable at the Part 2 ingress layer — see "M2 — deliberately deferred, not missed").

Full findings, severity, and reasoning: see the response given alongside this audit (not persisted verbatim here — token economy).

**C4 closed both deferred test-isolation reasons from C5.** `no_cache` in `test_logging.py` is no longer load-bearing for correctness (a Redis-down request now degrades instead of 500ing) — kept anyway for test speed and to keep unit tests off real network calls. Confirmed by a real end-to-end run with no mocking: unreachable Redis produced two `WARNING` log lines and a clean `502 downstream_error` in 727.8ms, not a crash.

**Verified baselines** (measured in WSL, not assumed):
- Test suite: **94 passed** from `app/` (5 → 16 after C1 → 28 after C5 → 38 after C2 → 44 after C3 → 48 after C4 → 56 after the C2/C4 config-naming follow-up → 62 after H1 → 67 after M1+H7 → 73 after H2+H3 → 87 after H4+H5 → 86 after removing percentile tracking → 94 after M4).
- **Tests are no longer CWD-dependent (fixed 2026-08-22, M7-CWD).** Before the fix: 35 of 94 failed from the repo root (had grown from 25 of 73 at the last audit, 3 of 48 before H1 — every new test that starts the app via `TestClient` inherited the failure). After: **94/94 pass identically from `app/`, the repo root, and `/tmp`.** `app/tests/conftest.py` pins CWD to `app/` in `pytest_configure()`.
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
- **M4 — a cache hit skips the downstream forward.** The cache becomes a deduplication / idempotency layer rather than a protobuf-decode cache, which is what "avoid re-processing previously seen payloads" actually implies and what makes the Redis dependency earn its place. **Fixed 2026-08-22** — see Decisions and changes and `docs/issues/010-cache-becomes-a-dedup-layer.md`. Sub-questions resolved below.
- **H1 — rules are loaded and validated once at startup.** No per-request disk read, no hot-reload. Invalid config is a startup failure, not a request-time 500. A rules change is a pod restart, which is an honest rollout story for Part 3.
- Order of work: make it start, make it visible, make it correct, make it survive Kubernetes, then hygiene.

**M4 sub-questions — resolved 2026-08-22** (were open questions blocking `cache.py`, now decided and implemented, see `docs/issues/010-cache-becomes-a-dedup-layer.md`):

- **What does a suppressed duplicate return to the client?** **Resolved: cache the actual downstream response and replay it byte-for-byte** — the user's call, and a better answer than either option originally on the table (a synthetic marker, or nothing). Fully transparent to the client; internally still a distinct `duplicate_suppressed` outcome.
- **Should the rules config hash be part of the cache key?** **Resolved: no** — accept and document the residual risk (a cached response can outlive a rules change for up to the TTL, same exposure that already existed from Redis persisting across restarts regardless of this decision).
- **Is 300s the right dedup window, and should it be configurable?** **Resolved: configurable** — `CACHE_TTL_SECONDS`, default 300.0, `.env`-configurable via the same shared validator as the other four operational settings.

**M2 decided and deferred** (was "still open from before" — see "M2 — deliberately deferred, not missed" above): app-level streaming enforcement is the scoped fix; an ingress-level cap is a Part 2 defense-in-depth addition (Backlog). Neither is implemented yet.

## Backlog / Later

Items discovered during the Part 1 review that intentionally belong to a later Part.

**Part 2 — Infrastructure & Deployment**
- Downstream URLs in `config/rules.json` are all `http://localhost:8001/pokemon`. These become per-environment ConfigMap values.
- **H1 consequence:** rules are read once at startup, so a rules ConfigMap change does nothing until the pods restart. The Deployment needs a pod-template checksum annotation over the rules ConfigMap (or an operator like Reloader) so a rules change actually rolls. Without it, a rules update looks applied in git and is silently inert in the cluster.
- `mock_service` is not in the wheel packaging (`packages = ["src/pokeproxy"]`) and binds `127.0.0.1`. Needs a deliberate containerization decision (L6).
- `preStop` hook and `terminationGracePeriodSeconds` are the manifest half of graceful shutdown (H7). The app-side hooks land in Part 1.
- Probe configuration encodes the "Redis does not gate readiness" decision (M1).
- **M2 — an ingress-level max-body-size cap** (e.g., an Ingress annotation) as a cheap, standard, defense-in-depth layer in front of the app-level fix — not a replacement for it (see M2 in the Prioritized backlog table).

**Part 3 — CI/CD & GitOps**
- `mock_service` keeps received payloads in an in-process list. If it ever runs more than one replica, the E2E check (post through the proxy, then read `/received`) can hit a different pod and see nothing. Pin it to a single replica or the E2E is flaky.
- `scripts/load_generator.py` is the natural seed for the E2E traffic generator. Its `sys.path.insert` hack and hardcoded default secret should be cleaned so CI can import it.
- **M4 is now implemented — the load generator will stop generating load as-is.** It picks from 12 fixed payloads (`POKEMON_DATA`, `random.choice` at `load_generator.py:92`), and protobuf serialization of identical field values is byte-identical, so there are exactly 12 distinct body hashes. Dedup now genuinely skips the forward on a repeat, so the first dozen requests exercise the downstream path and everything else for the next `CACHE_TTL_SECONDS` is suppressed. A 60s run at 10 rps would forward ~12 of 600 requests. Confirmed by test, not just predicted — `test_dedup.py`. The generator needs a varying field (nonce or timestamp) to stay useful for load testing, and Part 4 dashboards will read as near-zero forward rate until it has one.
- **M4 is now implemented — the post-deploy E2E must use a unique payload per run**, or flush the dedup key first. Re-running the same E2E payload inside the TTL produces no new downstream delivery — it replays the cached response instead — so the check fails on a healthy deployment. This is a correctness requirement for the Part 3 gate, not a nicety.
- Tests use relative fixture paths (`load_rules("config/rules.json")`) and only pass when CWD is `app/`. Fixed as part of M7 so CI is not CWD-dependent.

**Part 4 — Observability**
- Replace `/stats` with Prometheus instrumentation, reusing the Part 1 outcome-accounting seam (H4, H5).
- **M4 consequence, already satisfied by the seam:** `duplicate_suppressed` is already its own terminal outcome (implemented in M4, via the same `StatsRegistry.record_outcome()` seam H4 built) — carry the label through to Prometheus and make sure it's distinguishable on the dashboard from a genuine drop in inbound traffic.
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
| H2 | High | Downstream response headers copied verbatim to the client, including framing and hop-by-hop headers. | `proxy.py:132` (pre-fix) | 4 | **Fixed** — `docs/issues/008-header-hygiene.md` |
| H3 | High | Client headers forwarded downstream on a denylist basis. Denylists are always incomplete. | `proxy.py:34-45` (pre-fix) | 4 | **Fixed** — switched to an allowlist, `docs/issues/008-header-hygiene.md` |
| H4 | High | `request_count` only increments on success while `error_count` increments on failure, so `error_rate` reads 0.0 during a total outage. `bytes_received` is assigned rather than accumulated. Rejections and no-rule-matched are never counted. | `stats.py:29`, `proxy.py:87,95,101,162` (pre-fix) | 4 | **Fixed** — `docs/issues/009-outcome-accounting-and-unbounded-stats.md` |
| H5 | High | `_response_times` grows without bound and `bisect.insort` is O(n) per insert, so memory and CPU degrade with uptime. | `stats.py:15,19` (pre-fix) | 4 | **Fixed** — bounded to 1000 samples, `docs/issues/009-outcome-accounting-and-unbounded-stats.md` |
| H6 | High | Config assumes localhost, relative paths and a loopback bind. None of it survives a container. | `config/rules.json`, `config.py:15`, `mock_service/main.py:34` | 4 / P2 | Open |
| M2 | Medium | ~~`int(content_length)` unguarded, so a malformed header is a 500.~~ **First half disproved:** measured during C5 — uvicorn's httptools parser rejects `Content-Length: abc` with its own 400 before the handler runs, so `int()` never sees a non-digit string. Second half stands: body is fully buffered *before* the size check, so the 1 MiB limit does not actually limit anything — a genuine resource-exhaustion vector. | `proxy.py:173-183` (current) | 5 | **Deferred, deliberately — reviewed and root-caused 2026-08-22, fix fully scoped (stream + count via `request.stream()`, abort at 413 before finishing the read), not implemented. Also achievable at the Part 2 ingress layer as defense-in-depth — see "M2 — deliberately deferred, not missed" above.** |
| M6 | Medium | `POKEPROXY_PORT` is defined and documented but read by nothing. The real port comes from the uvicorn CLI. | `config.py:20`, `.env.example:3` | 5 | Open |
| M7 | Medium | ~~Five tests, all on decode/parse/match. Nothing covers `/stream`, HMAC, cache, Redis failure, downstream failure, headers or size limits.~~ **Disproven by the incremental regression suite** — each Wave fix added its own coverage; 94 tests span `/stream`, HMAC, cache hit/miss/failure (including a real cache **hit** end-to-end via `test_dedup.py`, added with M4), Redis failure, downstream timeout/error, headers (both directions), size limits, readiness, startup failure, and dedup. The one real remaining item — CWD-dependence — is **fixed**, see `docs/issues/011-test-suite-cwd-dependence.md`. | `tests/test_basic.py` (pre-fix framing) | 5 | **Fixed** |
| L1 | Low | A working HMAC secret is committed in `.env.example` and hardcoded as the load generator default. | `.env.example:1`, `load_generator.py:74` | 5 | Open |
| L2 | Low | Empty-name payloads rejected as "likely garbage input" — a heuristic wearing validation's clothes, with a misleading error message. | `config.py:83-84` | 5 | Open |
| L3 | Low | Error responses are opaque and uncorrelatable. `{"error": "downstream error"}` gives support nothing to search on. | `proxy.py:157,140,147` (current) | 5 | **Deferred, deliberately — reviewed and root-caused 2026-08-22, fix fully scoped (inject `request.state.request_id` at the 6 `_outcome_response` call sites + 2 `_forward_request` except blocks), not implemented. Prioritized M2/M4/M7-CWD instead — see "L3 — deliberately deferred, not missed" above.** |
| L5 | Low | `ruff` is configured with a good ruleset and nothing runs it. No type gate despite `# type: ignore` throughout. | `pyproject.toml` | 5 | Open |
| M4 | Medium | Cache costs a Redis round trip to save a microsecond-scale protobuf decode, and a hit still forwards downstream anyway. | `cache.py`, `proxy.py` (pre-fix) | 3 | **Fixed** — `docs/issues/010-cache-becomes-a-dedup-layer.md` |
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

**H2 + H3 — header hygiene in both directions (Wave 4).** `STRIP_HEADERS` (a denylist) is replaced by `ALLOWED_FORWARD_HEADERS` (an allowlist), currently empty — no original client header reaches downstream; the proxy already builds every header downstream needs itself (`Content-Type`, `X-Grd-Reason`, `X-Request-ID`), and nothing downstream reads anything else (confirmed: `mock_service` only reads `X-Grd-Reason`). New `_forwardable_response_headers` strips the RFC 7230 hop-by-hop set plus `Content-Length`/`Content-Encoding` from the downstream response before it reaches the client — kept as a corrected blocklist rather than an allowlist on this side, since the response comes from a trusted, configured downstream URL and an allowlist there risks silently dropping legitimate business headers.

Verified: 6 new tests (`test_headers.py`, 67 → 73) — unit tests on both filter functions directly, plus two end-to-end tests through `TestClient` with a mocked downstream confirming `Authorization`/`Cookie` never reach downstream while `X-Grd-Reason` does, and a mocked downstream's `Connection` header never reaches the client while a custom header does. `ruff check .` clean. Full detail in `docs/issues/008-header-hygiene.md`.

**H4 + H5 — outcome accounting fixed, unbounded response-time storage removed (Wave 4).** New `EndpointStats.record_request(is_error: bool)` replaces two independently-incremented counters with one atomic call, used at all three exit points of `_forward_request` — `error_count <= request_count` can no longer drift apart, closing the exact bug where `error_rate` read `0.0` during a total downstream outage. `bytes_received` changed from `=` to `+=`. New `StatsRegistry.record_outcome(outcome: str)` gives rejections and `no_rule_matched` — which have no downstream URL to key on — their own flat `{outcome: count}` map, via a new `_outcome_response()` helper in `proxy.py` that collapses every rejection branch into one call (mirrors C5's `request.state.outcome` seam, applied to accounting). `main.py`'s `internal_error` handler gets the same one-line treatment. `StatsRegistry.to_dict()` shape changes to `{"endpoints": {...}, "outcomes": {...}}` — no test or documented consumer relied on the old flat shape.

**Follow-up, same session — `_response_times`/`percentile()` deleted, not bounded.** First pass bounded the list to `deque(maxlen=1000)`. User pushed back: percentiles belong to Prometheus/Grafana in Part 4 (`histogram_quantile()` over real histogram buckets), and a hand-rolled bounded sample was complexity the app doesn't need to own for a capability with a known short shelf life. Deleted the structure and the method entirely instead of bounding it. `avg_response_time` untouched — it was already O(1) memory (`total_response_time`/`request_count`) and was never the source of the H5 bug.

Verified: 14 new tests (`test_stats.py`, 73 → 87) — unit coverage on both data structures, plus end-to-end proof through `TestClient` that 3 simulated downstream failures in a row now produce `error_rate == 1.0`, not `0.0`; a rejected request and a `no_rule_matched` request are both counted by outcome; an unhandled exception is counted as `internal_error`; `bytes_received` accumulates across two requests instead of being overwritten. `ruff check .` clean. Full detail in `docs/issues/009-outcome-accounting-and-unbounded-stats.md`.

**M4 — cache becomes a dedup layer (Wave 3).** `cache.py` now stores the downstream **response** (status, filtered headers, base64-encoded content in a JSON envelope), not the decoded payload — `get_cached_response`/`cache_response` replace `get_cached_pokemon`/`cache_pokemon`. A hit in `stream()` short-circuits immediately via `_duplicate_response()`: no decode, no rule match, no forward, `request.state.outcome = "duplicate_suppressed"`, recorded through H4's outcome-keyed stats seam, replays the stored response byte-for-byte. A miss proceeds as before; `_forward_request` caches the response itself, inline in its success branch, only when a real downstream response came back (`forwarded`) — `downstream_timeout`/`downstream_error` are never cached, so a duplicate arriving after downstream recovers gets a fresh attempt rather than a replayed failure. `Settings.cache_ttl_seconds` (`CACHE_TTL_SECONDS`, default 300.0) joins the four existing operational settings sharing `_check_positive_seconds`.

User's design call, and a genuine improvement over what was originally proposed (a synthetic "duplicate" marker): replaying the real response is fully transparent to the client, matching how idempotency-key caching works in production systems. Duplicate replays deliberately do **not** touch per-URL `EndpointStats` — no network call happened, so counting one there would misrepresent real downstream traffic volume.

Verified: `test_cache.py` rewritten for the new API (11 tests) — hit/miss, arbitrary binary content round-trips through the base64 envelope, C3's no-keyspace-scan regression preserved, C4's failure-degrades-to-miss/no-op regression preserved. `test_config.py` +2 parametrized cases for `CACHE_TTL_SECONDS`. New `test_dedup.py` (5 tests), end-to-end through `TestClient` with a fake Redis and a mocked downstream: a duplicate replays the cached response and downstream is called exactly once, not twice; the replay is counted as `duplicate_suppressed`; it does not inflate `EndpointStats.request_count`; a downstream failure is never cached — a retried duplicate after recovery gets a fresh, successful attempt; two different payloads are never deduplicated against each other. Full suite: 86 → 94. `ruff check .` clean. Full detail in `docs/issues/010-cache-becomes-a-dedup-layer.md`.

**M7-CWD — test suite pinned to `app/` regardless of invoking shell (Wave 5).** New `app/tests/conftest.py`, four lines: a `pytest_configure()` hook that `chdir`s to the directory containing the test files' own parent, computed from `Path(__file__)` rather than assumed from the invoking shell. No application code changed — `POKEPROXY_CONFIG` staying a relative path is correct for every real deployment (container `WORKDIR`, or `cd app && uvicorn ...` locally); this was purely a test-harness gap, not an app bug.

Verified by direct multi-directory invocation rather than a new pytest test (a test that verifies "pytest works" would be circular): 94/94 passed identically from `app/`, the repo root, and `/tmp` — up from 59 passed / 35 failed from the repo root before the fix. `ruff check .` clean. Full detail in `docs/issues/011-test-suite-cwd-dependence.md`.

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
