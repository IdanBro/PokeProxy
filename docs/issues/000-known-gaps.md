# D1 — Known gaps: found but not fixed in Part 1

**Severity:** Should fix (documentation gap found in the final Part 1 audit, 2026-08-22) · **Status:** Fixed (this document)

## Problem

Deliverable 2 of the assignment is "for each issue you find, write it up... don't just fix it in the code." `docs/issues/` had 11 write-ups (001-011, plus 012 for R1) covering every issue that was **fixed**. The issues found but deliberately **not** fixed — reviewed, root-caused, and either scoped-for-later or explicitly out of Part 1's scope — existed only as rows in `WORKLOG.md`'s backlog table. A reviewer who opens `docs/issues/` and sees 12 files has no way to tell that 14 more things were found and consciously not touched, versus simply missed.

## Production Impact

None directly — this is a communication gap, not a code gap. The risk it closes is a different one: without this record, "why didn't you fix X" has no answer beyond "check my notes," which is a weak position in exactly the kind of review this assignment is testing for.

## Decision

One consolidated document instead of 14 more `docs/issues/NNN-*.md` files. Each of these was reviewed in the same pass as the fixed issues and reasoned about at the time (see the tables and dedicated write-ups below, and for M2/L3 the "deliberately deferred" reasoning further down this document) — a full seven-section write-up per item would mostly restate that reasoning at several times the length. A table plus a short paragraph per item where the reasoning isn't self-evident is the right amount of documentation for "found, not fixed, here's why."

## The gaps

### Deferred to a later Part (already fits an upcoming Part's scope)

| ID | Sev | Problem | Target |
|----|-----|---------|--------|
| H6 | High | `config/rules.json` hardcodes `http://localhost:8001/pokemon`; `Settings.pokeproxy_config` defaults to a relative path; `mock_service` binds `127.0.0.1`. None of it survives a container. | **Part 2** — rules URL becomes a per-environment ConfigMap value, bind address becomes `0.0.0.0`, this is fundamentally a deployment-topology decision |
| M2 (partial) | Medium | An ingress-level `client_max_body_size`-equivalent cap as defense-in-depth in front of the app. The app-level half (streaming enforcement) is scoped below, not deferred to Part 2 | **Part 2** — Ingress annotation |
| L6 | Low | `mock_service` is not in `[tool.hatch.build.targets.wheel] packages`, and imports as a top-level module rather than a package. | **Part 2** — needs a deliberate containerization decision (separate image vs. bundled) before it's worth fixing |
| L5 | Low | `ruff` is configured with a real ruleset (`pyproject.toml`) and nothing runs it — verified clean today, so this is a missing gate, not a backlog of violations. No type-checker gate despite `# type: ignore` comments throughout. | **Part 3** — natural fit as a CI lint/type step |

### Reviewed, root-caused, fix scoped — deliberately not implemented in Part 1

**M2 (app-level half)** — fixed, see `docs/issues/031-request-body-buffered-before-size-check.md`.

**L3 — error responses carry no correlation ID in the body.** `{"error": "downstream error"}` and similar give support nothing to search on directly, unlike `main.py`'s own `internal_error` handler, which does include `request_id`. Inconsistent, and the one thing "useful error messages" names directly in the assignment that isn't fully met. Fix, fully scoped: inject `request.state.request_id` into the content dict at the 6 call sites already funneled through `proxy.py`'s `_outcome_response()` helper, plus the 2 `JSONResponse` literals in `_forward_request`'s `except` blocks. Deferred rather than fixed because the `X-Request-ID` response *header* already carries the same correlation ID today — this is a body-vs-header convenience gap, not a hard blocker, and was the one Low-severity item in an otherwise Medium/High "should fix" set. A related, separately-decidable question surfaced during review and is also left open: `rejected_signature_missing` and `rejected_signature_invalid` currently return identical body text (`"invalid signature"}`) despite being distinguishable outcomes in the logs.

### Documented only — needs a decision this project can't make unilaterally

**M3 — no replay protection on signed payloads.** The HMAC covers the body only, so a captured, validly-signed request stays valid forever — nothing binds a signature to a point in time. The fix (a signed timestamp inside the HMAC input, a bounded acceptance window, a nonce cache in Redis to catch replays inside that window) is a **protocol change**: it changes what the client must sign, which means every legitimate client — including the load generator — needs to change too. Not something to decide inside a code-review pass; recorded as a known gap for whoever owns the wire protocol.

### Low severity, genuinely low priority

| ID | Sev | Problem | Why it stays open |
|----|-----|---------|--------------------|
| L1 | Low | A working HMAC secret (`dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg==`) is committed in `.env.example` and hardcoded as the load generator's default (`load_generator.py:74`). | Standard for a documented local-dev convenience — the risk is someone reusing it in a real deployment, which C1's fail-fast startup validation doesn't and can't prevent (a *valid* key is a valid key regardless of who else has it). Real mitigation is process (secret rotation, a K8s Secret in Part 2), not a code fix |
| L2 | Low | Empty-name payloads are rejected as `"Decoded protobuf has empty name — likely garbage input"` (`config.py:83-84`) — a heuristic wearing validation's clothes, and the message overstates its own confidence | Cosmetic; the request is correctly rejected either way, only the stated reason is imprecise |
| M6 | Medium (impact), Low (cost) | `POKEPROXY_PORT` is defined in `Settings` and documented in `.env.example`/`README.md`, but nothing reads it — the real port comes from the uvicorn CLI's `--port` flag | Dead configuration is a real "useful error messages"-adjacent gap (an operator setting it reasonably expects it to work), but the fix is either wiring it into the documented `uv run uvicorn ...` startup command or deleting the field — a one-line call either way, correctly sequenced after Part 2 decides how the container actually starts the process |

## Also recorded in the final audit, not yet acted on

Found in the same 2026-08-22 audit pass that produced R1/D1; nice-to-have severity, so grouped here rather than given individual files:

| ID | Problem | Evidence |
|----|---------|----------|
| R2 | Expected, *handled* Redis failures log a full traceback each (`cache.py:20,50`, `exc_info=True`) even though the JSON `error` field already carries `ConnectionError: ...`. Measured: **388 of 418 log lines were traceback text** for 8 handled warnings in a single probe run — 93% of log volume for events that are already degraded-gracefully, not crashes. | live probe |
| R3 | ~~A downstream **5xx** is cached and replayed for the full `CACHE_TTL_SECONDS`.~~ **Resolved** as a side effect of `docs/issues/028` (non-2xx downstream responses are no longer cached at all, not just 5xx) | `docs/issues/028-non-2xx-downstream-responses-treated-as-success.md` |
| R4 | A config failure emits the intended single `CRITICAL` line and then uvicorn's own ~20-line `SystemExit: 1` lifespan traceback. The actionable line comes first and is correct — this is noise, not a defect | live probe |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| This document itself will drift if an item is fixed later and the corresponding row isn't removed | Same discipline as `WORKLOG.md`'s backlog table already requires — whichever issue is picked up next should update or remove its row here as part of that change, not leave a stale "known gap" that's actually fixed |
| L3 already had a scoped fix described in `WORKLOG.md` before this document existed (M2 app-level's fix is now shipped — see `docs/issues/031`) | Consolidated here for discoverability; day-by-day narrative now lives in `docs/planning/AI_WORKFLOW.md`, this file is the standing reference a reviewer would look for first |
