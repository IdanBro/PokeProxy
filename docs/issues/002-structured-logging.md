# C5 — No logging: every failure path returned JSON and vanished

**Severity:** Critical · **Wave:** 2 · **Status:** Fixed
**Files:** `app/src/pokeproxy/logging_config.py` (new), `main.py`, `proxy.py`, `config.py`

## Problem

No logging anywhere in the application — `grep` for `import logging|getLogger|structlog` across `src/`, `mock_service/`, `tests/` returned nothing, and no logging dependency existed.

Uvicorn's plaintext access line was the only output, so the service was not silent but **unstructured, uncorrelated and semantically blank**. Measured on a running instance (5 requests, 116 log lines total):

| Case | Client saw | Everything logged |
|---|---|---|
| bad HMAC signature | 401 | `INFO: ... "POST /stream HTTP/1.1" 401 Unauthorized` |
| **missing** signature header | 401 | `INFO: ... "POST /stream HTTP/1.1" 401 Unauthorized` |
| valid sig, Redis down | 500 `Internal Server Error` | ~100-line raw traceback |

Three specific gaps:

1. **Bad signature and missing signature were byte-identical** in logs and response — an attacker and a broken client are indistinguishable.
2. **`no_rule_matched` returns HTTP 200 with an empty body** (`proxy.py:158`) and logged nothing. A rules misconfiguration dropping 100% of traffic is invisible.
3. **No line carried latency, correlation ID, byte counts, or reason.** `stream()` has 9 terminal outcomes; logs distinguished them only by status code, and two share status 200.

## Production Impact

A partial outage where some payloads hit a bad rule and silently return 200 produces **no signal at all** — not in logs, not in `/stats` (H4 zeroes `error_rate`), not in any probe. The service reports healthy while dropping traffic.

Traceback-per-500 is also a volume concern: ~100 lines per failed request means a Redis outage at modest RPS floods stdout and the node's log pipeline. C5 deliberately does **not** solve that — tracebacks are kept in full on purpose (see Tradeoffs). What it fixes is that every traceback now has a structured record attached carrying the request ID, outcome and duration, so the volume is at least navigable.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Library | stdlib + JSON formatter · `structlog` · `python-json-logger` | **stdlib** |
| Uvicorn's logs | leave plaintext · unify via `dictConfig` · unify + replace access log | **unify + replace** |
| Correlation header | `X-Request-ID` · `X-Correlation-ID` · `X-Grd-Request-Id` | **`X-Request-ID`** |
| Outcome recording | middleware only · handler only · handler labels, middleware emits | **split** |
| Config surface | `LOG_LEVEL` only · add `LOG_FORMAT=json\|console` | **`LOG_LEVEL` only** |

## Decision

**stdlib `logging`.** No new dependency, and — the deciding factor — uvicorn and starlette already log through stdlib, so one setup unifies their output with ours. `structlog` is nicer to write but is a dependency bought for ergonomics at a scale that does not need it.

**Replace uvicorn's access log.** It structurally cannot carry a correlation ID, latency, or outcome. Configuration is applied at *import time* in `main.py`, because uvicorn configures its own logging before importing the app — doing it in `lifespan` would leave the first few startup lines plaintext.

**`X-Request-ID`.** The `X-Grd-*` prefix is right for application protocol (`X-Grd-Signature`, `X-Grd-Reason`); a correlation ID is infrastructure. Ingress controllers and service meshes already inject `X-Request-ID`, and inventing our own name breaks the trace at the edge.

**Handler labels, middleware emits.** Middleware alone cannot tell "forwarded 200" from "no rule matched 200"; the handler alone cannot guarantee exactly-once or catch unhandled exceptions. Handlers set `request.state.outcome`; the middleware is the single place that reads it. An unlabelled path logs `unknown` rather than `ok`, so a terminal path I missed shows up as a gap instead of hiding as a success.

**Deliberately simple:** no `contextvars`, no outcome enum, no console format. The middleware already holds the request, so the ID needs no ambient propagation at this size.

## Implementation

New `logging_config.py` (~65 lines): a `JSONFormatter` that emits one JSON object per record and renders any `extra=` field as a structured key, plus `setup_logging()` which installs it on the root logger, re-points uvicorn's loggers at it, and disables `uvicorn.access`.

**Tracebacks stay multi-line, by deliberate choice.** The JSON record carries a short `error` summary (`ConnectionError: Error 111 connecting to localhost:6379...`) so failures stay greppable and alertable, and the full traceback follows the record as ordinary text. Escaping a traceback into the JSON object would make it technically one line but an unreadable one — nobody skims an escaped traceback during an incident.

`main.py` gains the import-time setup call, a `_load_settings()` that turns a config `ValidationError` into one CRITICAL line, and the access-log middleware. The middleware also **catches unhandled exceptions and returns a JSON 500 carrying the `request_id`**, so support has something to search on rather than an opaque `Internal Server Error`. The traceback is still logged in full, now preceded by a record tying it to that request ID.

`proxy.py` gains nine one-line `request.state.outcome = "..."` assignments and propagates `X-Request-ID` downstream. The 401 branch was split so a missing header and an invalid signature are separate outcomes.

Log fields: `ts, level, logger, msg, request_id, method, path, status, duration_ms, outcome`.

Outcome values (these become Prometheus labels in Part 4, so the set is deliberately small and bounded): `forwarded`, `no_rule_matched`, `rejected_signature_missing`, `rejected_signature_invalid`, `rejected_protobuf`, `rejected_too_large`, `downstream_timeout`, `downstream_error`, `internal_error`.

Also folded in the C1 leftover: a bad configuration is now one CRITICAL line instead of a raw pydantic traceback.

## Verification

Run in WSL Ubuntu against `app/.venv` (Python 3.13).

| Check | Result |
|---|---|
| Suite from `app/` | **28 passed** (was 16); 12 new in `tests/test_logging.py` |
| Same 5-case probe as the "before" measurement | **116 plaintext lines → 15 structured JSON records**, plus 88 readable traceback lines from the Redis-down case |
| Two 401 cases | now `rejected_signature_invalid` vs `rejected_signature_missing` |
| Redis-down 500 | one JSON record with an `error` summary and the request ID, followed by the full readable traceback; client gets `{"error":"internal error","request_id":"..."}` |
| Uvicorn's own startup/shutdown lines | emitted as JSON — unification confirmed, not assumed |
| `/health` | produces no access line |
| Bad config startup | one CRITICAL line naming the field and the fix |
| `ruff check .` | clean |

Tests cover the formatter (one JSON record per log call, with the traceback following as plain text rather than escaped into the object), correlation (generated when absent, echoed when supplied), and the outcome label for each reachable terminal path — including `no_rule_matched`, the invisible-failure case.

Two constraints shaped the tests, both caused by bugs still open:

- **C4** — Redis is unguarded, so any request with a valid signature 500s before reaching the decode. A `no_cache` fixture patches the cache out so C5 is testable in isolation.
- **C2** — `_forward_with_retry` retries forever, so a test reaching the forward path with the downstream down would hang. Every test therefore uses a payload matching no rule. **Both constraints disappear once Wave 3 lands**, and the forward-path outcomes (`forwarded`, `downstream_timeout`, `downstream_error`) should get direct tests then.

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| `forwarded`, `downstream_timeout`, `downstream_error` are labelled but **not yet covered by a test** | Blocked by C2's unbounded retry. Add tests in Wave 3 |
| **The stream is not pure line-delimited JSON**, because tracebacks span lines | Deliberate: readability during an incident beats parser purity. Consequence for Part 2 — a log shipper (Fluent Bit, Promtail) needs a multiline rule to attach traceback lines to their record, or they arrive as loose unparsed lines |
| Uvicorn still logs a `SystemExit` traceback after the clear CRITICAL config line | Redundant after the CRITICAL line, but harmless. Suppressing it needs `os._exit()`, which risks losing the unflushed CRITICAL line — not worth it |
| Middleware swallows unhandled exceptions and returns JSON 500 | Slightly beyond "logging", but it is what stops the traceback flood and gives errors a correlation ID. Flagged rather than hidden |
| No `contextvars` | Logs emitted *inside* handlers will not auto-carry the request ID. Acceptable now; revisit if handler-level logging grows |
| Log field names are now a contract | The Part 3 E2E will assert on `request_id` and `outcome`. Renaming later breaks that gate |
| Import-time `setup_logging()` in `main.py` | Impure, and it clears root handlers — which broke a first draft of the tests depending on import order. Tests now import `main` at module scope to make the ordering explicit |
| `/stats` still unauthenticated and leaking downstream URLs | **M5**, deferred to Part 4 |
| `/stats` counters still wrong | **H4**, Wave 4. C5 built the seam; H4 rewires counters through it |
