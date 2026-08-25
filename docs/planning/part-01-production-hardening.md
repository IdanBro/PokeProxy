# Part 1 — Code Review & Production Hardening

Planning notes written after the initial read-only review, before any code changed.

## What I am actually solving

The assignment frames Part 1 as "find the bugs", but the deliverable is broader: the service has to be startable from its documented configuration, observable enough to debug at 3 AM, bounded in how it fails, and well-behaved through a Kubernetes rollout. Bug-fixing is necessary but not sufficient.

So I am treating Part 1 as everything that must be true *before* containerization is even worth attempting. If I skip straight to a Dockerfile, I am packaging a service that cannot start, cannot be debugged, and hangs on shutdown.

## Approach

Fix in waves, ordered by dependency rather than by severity. The organizing principle:

> Make it start → make it visible → make it correct → make it survive Kubernetes → hygiene.

Severity alone would put the infinite retry loop (C2) first, since it is the worst bug in the repo. I deliberately did not do that — see "Order of work" below.

Each issue is a separate change with its own regression test, its own verification, and its own write-up under `docs/issues/`. I would rather have eight small defensible diffs than one large one I cannot explain line by line in an interview.

## Order of work

**Wave 1 — Startup correctness (C1).** You cannot deploy what will not boot. This is also the cheapest change in the backlog and it unblocks every subsequent manual test.

**Wave 2 — Observability floor (C5).** Structured JSON logging to stdout, a request/correlation ID, and a single seam where each terminal outcome is recorded.

**Wave 3 — Stop the bleeding (C2, C3, C4, H1).** The four changes that turn dependency failures from outages into degradations: bounded retries on a shared client, `KEYS` to `GET`, best-effort cache with real socket timeouts, and rules loaded and validated once at startup.

**Wave 4 — Lifecycle and correctness (M1 + H7, then H2 + H3, then H4 + H5).** Health/readiness split and graceful shutdown are one design and land together. Then header hygiene in both directions. Then the outcome-accounting fix.

**Wave 5 — Hardening and hygiene (M2, M6, L1, L2, L3, then M7, then L5).** Body limits, dead config, secret hygiene, error envelope, the test-coverage gap-filling pass, and a lint/type gate.

### Why logging comes before the worst bug

This is the one ordering decision worth defending explicitly, because the obvious alternative is to fix C2 first.

I put logging second for two reasons:

1. **It touches every module.** Doing it first means the Wave 3 and 4 fixes get written in the new style. Doing it last means retrofitting log lines into code I just finished changing, which is a second pass over the same files.
2. **Wave 3 is entirely "prove the failure mode changed".** Without logs I can assert that the retry is now bounded, but I cannot demonstrate it. Spending one cheap, low-risk change to make the expensive changes provable is worth a slot in the order.

The cost is that the worst bug in the repo stays unfixed for one extra change. I accepted that because nothing is in production and the sequencing buys verifiability for everything after it.

## Alternatives I considered

**C1 — which name wins.** Four options: rename the code field to `pokeproxy_secret`; align the docs to `POKEPROXY_HMAC_KEY`; accept both via `AliasChoices`; or rename to something maximally explicit like `POKEPROXY_HMAC_SECRET_B64`.

I chose aligning the docs to the code. `POKEPROXY_HMAC_KEY` states the algorithm and the role; "secret" states neither. I rejected the alias option specifically: aliasing is a migration tool, and there is nothing to migrate. A greenfield service that accepts two names for its secret is starting with debt.

**C1 — minimum key length: 16 bytes.** Separate decision, because it determines how far C1 reaches.

RFC 2104 recommends an HMAC key at least as long as the hash output, which is 32 bytes for SHA-256. But the committed dev secret decodes to 25 bytes, so a 32-byte floor would reject it and force regenerating the secret in both `.env.example` and `scripts/load_generator.py` — turning a configuration fix into one that also edits the load generator.

I chose 16 bytes (128 bits). It is still far beyond brute-force reach for an HMAC key, it is a common industry floor, and it keeps C1 to exactly one concern: the service must refuse to start on a secret that is absent, malformed, or trivially weak. The three failure modes I actually care about — wrong variable name, a non-base64 placeholder like `changeme` decoding to 6 bytes of garbage, and an empty value decoding to a zero-length key — are all still caught.

Residual risk, accepted: a 16-byte key is below the RFC recommendation, and the repo keeps a real working secret in version control until L1 is fixed in Wave 5. Both are deliberate. If I later want the 32-byte floor, raising it is a one-line change plus a secret rotation, and rotation is something the deployment has to support anyway.

**Metrics — fix `/stats` or replace it.** `/stats` is wrong in a way that matters (`error_rate` reads 0.0 during a total outage), so leaving it is not an option. But Part 4 replaces the whole thing with Prometheus, and polishing it now means instrumenting twice.

Decision: in Part 1 I fix the *accounting seam* — restructure so there is exactly one place each terminal outcome is recorded, and make sure that set covers rejections and no-rule-matched, which are currently invisible. Part 4 then swaps the backend behind that seam instead of re-touching every file. I also decide the metric names, labels and cardinality now so the two parts agree.

**Readiness — should it check Redis.** The tempting answer is yes, since a readiness probe that checks nothing is not a probe. I decided no, and the reasoning is the point: if readiness checks Redis, a single Redis blip un-readies every pod simultaneously and takes down a service that was designed to degrade gracefully without a cache. Correlated readiness failure across an entire deployment is worse than the degradation it is trying to prevent.

So: liveness means the process is responsive, readiness means config is loaded and the things I genuinely cannot serve without are up. Redis is not one of them. This decision has to be made in Part 1 because Part 2 encodes it in the probe configuration.

**H1 — hot-reload or startup-only.** Reading `rules.json` per request is one way to get hot-reload, and it is the wrong way. The two real options are load-once-at-startup (a rules change is a pod restart) or an explicit safe reload (mtime check or SIGHUP, parse into a new list, swap atomically, never serve a half-parsed config).

**Decided: startup-only.** It is simpler, it turns a config error into a fail-fast startup failure instead of a request-time 500, and "a rules change is a rollout" is a perfectly honest GitOps story for Part 3. Hot-reload would also have to be *proven* in the Part 5 E2E, which is real work for a feature nobody asked for.

The consequence I have to handle in Part 2: rules live in a ConfigMap, and a ConfigMap edit will not reach a running pod that only reads the file at startup. Without a pod-template checksum annotation over the rules ConfigMap (or an operator like Reloader), a rules change looks applied in git and is silently inert in the cluster. That is a worse failure than the per-request read I am removing, because it fails quietly. Recorded in the backlog.

**M4 — what the cache is for. Decided: a cache hit skips the downstream forward.**

Today the cache keys on a SHA-256 of the body and stores the decoded protobuf, so it spends a Redis round trip (hundreds of microseconds) to save a protobuf decode (microseconds), and a hit still forwards downstream anyway. As written it makes the median request slower and adds a whole failure domain in exchange for nothing.

Skipping the forward on a hit turns it into a deduplication / idempotency layer. That is what "avoid re-processing previously seen payloads" actually implies, and it is the only reading under which the Redis dependency earns its place in the architecture.

This is a behaviour change, not a bug fix, and it has consequences I would rather write down now than discover in Part 3:

- **Duplicate suppression is best-effort, because C4 makes the cache best-effort.** If Redis is down, a lookup failure is treated as a miss and the payload is forwarded. So a Redis outage produces duplicate deliveries, not dropped ones. That is the right trade — at-least-once beats silently discarding traffic — but it means **the downstream service must be idempotent**, and that assumption needs stating out loud rather than being buried in the cache layer.
- **The load generator largely stops generating load.** It picks from 12 fixed payloads and protobuf serialization is byte-identical for identical field values, so there are exactly 12 distinct body hashes. After the first dozen requests, everything inside the TTL is suppressed — a 60s run at 10 rps would forward roughly 12 of 600 requests. The generator needs a varying field before it is useful again, and Part 4 dashboards will read as a near-zero forward rate until it has one.
- **The post-deploy E2E must use a unique payload per run.** Re-running the same payload inside the TTL produces no new downstream delivery, so the Part 3 gate would fail against a perfectly healthy deployment. Unique payload per run, or flush the key first.
- **Suppressed duplicates need their own metric.** If they are not a distinct terminal outcome, suppressed traffic vanishes from the numbers exactly like no-rule-matched does today, and the service looks healthy while requests disappear.
- **It interacts with H1.** A payload inside the dedup window is suppressed *before* routing, so a rules change does not apply to it for up to one TTL — even after the pod restart that H1 requires. Mixing the rules config hash into the cache key would fix that automatically, at the cost of a re-delivery burst on every rules change. Left open.

Three sub-questions fall out of this: what a suppressed duplicate returns to the client (the cache holds the decoded payload, not the downstream response, so there is no prior result to replay), whether the config hash joins the cache key, and whether the 300s TTL should become configuration now that it is a business-visible dedup window rather than an implementation detail.

## Constraints this sets for later Parts

Recorded here so I do not re-litigate them:

- **Env vars are the configuration interface.** `.env` is local-dev sugar only. Secret becomes a K8s Secret, rules become a ConfigMap, downstream URL becomes per-environment.
- **Redis does not gate readiness.** Part 2 probe config depends on this.
- **Bounded deadlines everywhere.** Part 3 E2E can only be deterministic if failures fail in bounded time; unbounded retries make every failure look like a hang. Retry policy also defines what a latency alert threshold can mean in Part 4.
- **One outcome-accounting seam.** Part 4 reuses it rather than rewriting the handler.
- **Log format and correlation header name are fixed in Part 1** so the Part 3 E2E can assert on them.
- **The mock service is single-replica by necessity.** It holds received payloads in an in-process list, so the Part 3 E2E (post through the proxy, then read `/received`) breaks the moment it scales past one pod.
- **Downstream must be idempotent.** Dedup is best-effort by construction, so a Redis outage means duplicate deliveries.
- **Traffic generation must vary its payloads.** Both the load generator and the Part 3 E2E now depend on payload uniqueness to exercise the forward path at all.
- **A rules ConfigMap change must force a pod restart.** Startup-only loading means an inert ConfigMap edit otherwise.

## Definition of done for Part 1

- Service starts from `.env.example` with no edits, and fails loudly and specifically when configuration is wrong. **Met (C1, H1).**
- Structured logs on stdout with a correlation ID on every request and every failure path. **Met (C5).**
- Redis down means degraded latency, not 500s. Downstream down means a bounded, correctly-coded error, not a hang. **Met (C4, C2).**
- Shutdown drains rather than hangs. **Met, app-side (M1+H7).** K8s-side (`preStop`, grace period) is correctly Part 2 scope, not a Part 1 gap.
- Every fix has a regression test that fails without it. **Met** for every issue actually fixed (C1-C5, H1, M1+H7, H2+H3, H4+H5, M4) — **94 tests**, verified `pytest -q` from `app/`, from the repo root and from `/tmp` (M7-CWD).
- Every fix has a write-up under `docs/issues/`. **Met for fixed issues** — 11 write-ups (001-011) cover all 14 fixed issue IDs (three are intentionally combined: M1+H7, H2+H3, H4+H5). **Not met for found-but-unfixed issues** — the 11 open/deferred IDs live only in the `WORKLOG.md` backlog table; see finding D1 in the final audit note below.
- Anything not verified by execution is explicitly labelled as not verified. **Met** for what's documented so far.

### Audit note (2026-08-22)

This "Definition of done" covers the changes actually made through H2+H3 — it does **not** mean the Wave 4/5 backlog this same planning doc created is empty. A full requirement-by-requirement audit against `README_HOME_ASSIGNMENT.md` found several assignment-named or self-committed items still open: H4 (outcome-accounting seam — explicitly promised above), H5 (unbounded memory growth), L3 ("useful error messages" — named directly in the assignment), M2 (body size limit doesn't limit), and M4 (dedup — decided in this document but never implemented in code; the Part 3/4 "M4 consequences" sections below describe a state the code is not yet in). M7's original finding is largely disproven by the incremental test suite, but re-scoped: H1 turned a CWD-relative config-path bug into a 25-of-73 test failure when run from the repo root instead of `app/` (was 3-of-48 before H1) — safe in production (fixed container `WORKDIR`) but a real risk for Part 3 CI. Full severity breakdown given to the user alongside this audit, not duplicated here.

**Update — H4+H5 fixed (`docs/issues/009-outcome-accounting-and-unbounded-stats.md`). L3 and M2 reviewed and deliberately deferred, not fixed.** Error responses carry no `request_id` in the body (only `main.py`'s `internal_error` handler does), inconsistent and worth closing — but it's the one Low-severity item in the SHOULD FIX set, correlation is still possible via the `X-Request-ID` response header today, and the fix was fully scoped during review (inject `request.state.request_id` at `proxy.py`'s `_outcome_response()` call sites plus two `_forward_request` except blocks) rather than implemented. M2's malformed-`Content-Length` half was already disproved during C5; the live half — `request.body()` fully buffers before the size check ever runs, a real resource-exhaustion vector — was also fully scoped (stream via `request.stream()`, count bytes, abort at 413 before finishing the read) but deliberately not implemented: this class of protection is also achievable at the Part 2 ingress layer, recorded there as a defense-in-depth addition. Neither deferral is an oversight — full reasoning for both in `docs/issues/000-known-gaps.md`'s "Reviewed, root-caused, fix scoped" section.

**Update (final review pass) — M2's app-level half is now fixed.** See `docs/issues/031-request-body-buffered-before-size-check.md`; L3 remains deferred as described above.

### Final audit note (2026-08-22, second pass)

Second requirement-by-requirement pass over `README_HOME_ASSIGNMENT.md` Part 1, this time with live execution rather than reading alone. No code changed.

**Verified by execution, not asserted:** `ruff` clean; 94 tests pass from three different working directories; the service starts from `.env.example` verbatim; a 10-case live probe through a running proxy and mock downstream returns the right status and outcome for every path (forward, no-rule-matched, both 401s, bad protobuf, oversize, `X-Request-ID` echo); Redis being down produces warnings and **zero** 5xx; `SIGTERM` drains and exits cleanly in 112 ms; the HMAC key value never appears in log output.

**No BLOCKER.** Every "definition of done" bullet above holds under execution.

**Two SHOULD FIX findings, both new — both now fixed:**

- **R1 — the retry policy could not retry a slow downstream.** The httpx per-attempt timeouts were hardcoded at `main.py:70` (`read=10.0`) while the retry budget `FORWARD_DEADLINE_SECONDS` defaults to the same 10.0, so the first attempt consumed the entire budget: measured **1 attempt of 3 in 10.17 s** against a socket that accepts and never responds, versus 3 attempts in 0.48 s against a refused connection. Slow/hung is the more common production failure, so `FORWARD_MAX_ATTEMPTS` was inert exactly where retries would matter — and `app/README.md` documented the knob as if it worked. **Fixed** — new `FORWARD_ATTEMPT_TIMEOUT_SECONDS` (default 3.0), validated at startup to be strictly less than the deadline. Re-measured against the same socket: **3 of 3 attempts in 9.70 s**. `docs/issues/012-retry-attempt-timeout.md`.
- **D1 — issue documentation covered only what was fixed.** Deliverable 2 asks for a write-up per issue *found*. `docs/issues/` had 11 files covering the 14 fixed IDs; the 11 found-but-unfixed IDs (M2, M3, M5, M6, H6, L1-L6) were recorded only as backlog rows in `WORKLOG.md`. **Fixed** — one consolidated `docs/issues/000-known-gaps.md`, grouped by disposition (deferred to a later Part / scoped-but-not-implemented / needs a protocol decision / low priority), also covering R2/R3/R4 below.

**Three NICE TO HAVE findings, still open, tracked in `docs/issues/000-known-gaps.md`:** R2 (handled Redis failures log a full traceback each — 388 of 418 log lines in the probe run were traceback text), R3 (a downstream 5xx is cached and replayed for the full TTL; issue 010's "cache any real downstream response" reasoning holds for 4xx business answers but is weaker for a transient 503), R4 (a config failure emits the intended single `CRITICAL` line *and then* uvicorn's own `SystemExit` traceback — noise, removable by a Part 2 entrypoint preflight).

**Deferrals re-confirmed, unchanged, all now indexed in `docs/issues/000-known-gaps.md`:** M2 (ingress-layer defense-in-depth in Part 2; the app-level streaming fix was deferred at the time but is now shipped — see `docs/issues/031`), L3 (`request_id` in error bodies), H6 (config assumes localhost — a Part 2 topology decision), M3 (replay protection — a protocol change), M5 and L4 (Part 4), L6 (Part 2), L5 (Part 3), L1, L2, M6.

**Part 1 is functionally complete as of this fix.** Every BLOCKER/SHOULD FIX finding from both audit passes is closed (fixed or, for pre-existing backlog items, deliberately deferred with reasoning on record). 101 tests pass from `app/` and the repo root; `ruff check .` clean. Next work is Part 2.
