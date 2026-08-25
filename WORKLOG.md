# PokeProxy Engineering Work Log

Persistent project state. Session-by-session AI collaboration record: `docs/planning/AI_WORKFLOW.md`. Per-part design and audit trail: `docs/planning/part-0N-*.md`. Per-issue write-ups: `docs/issues/`.

## Current State

PokeProxy is fixed and hardened (Part 1), containerized and deployed via Helm to a local k3d cluster plus a second k3d stand-in for prod (Part 2), delivered through a GitHub Actions CI → Argo CD GitOps pipeline with a gated post-deploy E2E check and a real rollback workflow (Part 3), instrumented with Prometheus/Grafana (Part 4), and brought up end-to-end with a single `make up` (Part 5). All 5 required Parts are complete, live-verified, and merged to `main` (`8b7b9cb`, via PR #12). No bonus part attempted — out of scope for this engagement.

## Status by Part

| Part | What shipped | Design & audit trail | Status |
|---|---|---|---|
| 1 — Production Hardening | 16 issues fixed (`docs/issues/001-016`): HMAC config validation, structured JSON logging, bounded forward retry, guarded/timeout-bounded Redis calls, startup-time rules load, liveness/readiness split, header hygiene both directions, correct outcome accounting, cache→dedup layer, CWD-independent tests. Three more found and fixed in the final review pass (`028-030`): non-2xx downstream responses counted as success and cached, unguarded cache deserialization, unbounded downstream response body | `docs/planning/part-01-production-hardening.md` | Done |
| 2 — Infrastructure & Deployment | Helm chart (pokeproxy + redis + mock-downstream), sealed-secrets for the HMAC key, NetworkPolicies, Pod Security Admission `restricted`, resource requests/limits, liveness/readiness probes, graceful shutdown wiring, PodDisruptionBudget | `docs/planning/part-02-infrastructure-deployment.md` | Done |
| 3 — CI/CD & GitOps | GitHub Actions CI (lint/test/build/sign/SBOM), Argo CD GitOps to dev + prod stand-in, digest-pinned `promote` job, PostSync E2E as a real `helm --atomic` gate, `rollback.yml`, all 3 rollback scenarios (bad tag, wrong-rule regression, real `rollback.yml` dispatch) executed live against real prod | `docs/planning/part-03-cicd-gitops.md` | Done |
| 4 — Observability | Prometheus metrics via `prometheus-client`, `kube-prometheus-stack` in dev + prod, 18-panel Grafana dashboard (14 data panels across 4 rows), 3 justified alerts (2 forced end-to-end through Alertmanager against induced failures) | `docs/planning/part-04-observability.md` | Done |
| 5 — Automation | `make up` / `make dev` / `make down`, Tilt-orchestrated deployment (`ext://helm_resource`, a real `helm upgrade --install`, not `helm template`), thin Makefile wrapper for cluster lifecycle, fail-loud `preflight.sh`, automated sealing-key mint+reseal, cold-start and idempotency both live-verified | `docs/planning/part-05-automation.md` | Done — merged, promoted (`8b7b9cb`) |

## Deliverables Checklist

Against `README_HOME_ASSIGNMENT.md`'s literal list.

| # | Deliverable | Status |
|---|---|---|
| 1 | Fixed and hardened application code | Done — Part 1, `docs/issues/001-012, 028-031` |
| 2 | Issue documentation per bug/issue | Done — `docs/issues/000-030` |
| 3 | Dockerfile(s) | Done — `app/Dockerfile`, `Dockerfile.mock`, `Dockerfile.e2e` |
| 4 | Kubernetes manifests / Helm chart | Done — `deploy/helm/pokeproxy` |
| 5 | CI/CD pipeline + post-deploy verification + rollback story | Done — `.github/workflows/ci.yml`, `rollback.yml`, all 3 rollback scenarios executed live |
| 6 | Monitoring: metrics, dashboard(s), alert rule(s) with justification | Done — Part 4 |
| 7 | One-command bootstrap + teardown | Done — `make up`/`make down`, committed and verified clean-machine |
| 8 | Root README explaining layout + bootstrap | Done — `README.md` |
| 9 | Planning artifacts per part | Done — `docs/planning/part-01` through `part-05`, `AI_WORKFLOW.md` |

Bonus: not attempted (assignment says "pick at most one, only if time remains").

## Known Gaps

Part 1 issues found but deliberately not fixed, with reasoning: `docs/issues/000-known-gaps.md`.

## Backlog / Later

Open items from Parts 2-5, carried forward with the reason each was deferred rather than fixed. Full evidence for each lives in the linked planning doc's audit section.

| Item | Why deferred | Ref |
|---|---|---|
| Redis has no AUTH | NetworkPolicy already scopes access to the app namespace; cache holds only hashed/short-TTL response data, no credentials | part-02 |
| No pod anti-affinity for pokeproxy replicas | PDB now caps voluntary eviction to 1, but same-node scheduling is still possible; real fix is a prod-only `podAntiAffinity` override, untestable on this single-node k3d cluster | part-02 |
| Dev images are short-sha tagged, not digest-pinned | Prod path is already digest-pinned via the `promote` job; Tilt's rebuild-on-change loop makes digest pinning low-value in dev | part-02 |
| `preStop.sleep` requires Kubernetes ≥1.30 | Compatibility note, not a bug — matches the k3d default used here | part-02 |
| Mock service port hardcoded in the image | Needs a deliberate config-flexibility decision only if the image is reused outside this assignment | part-02 |
| `promote` doesn't assert its written digests are pullable | Nice-to-have hardening on the promote job | part-03 (F-14) |
| `already_sealed()` doesn't verify decryptability — a present-but-foreign sealing key still skips the reseal | Narrower case of the clean-clone fix already shipped (P5-1/P5-2); needs a real decrypt-probe | `docs/issues/026` |
| No rebase-conflict handling in `promote`/`rollback` | Nice-to-have hardening | part-03 (F-16) |
| Fork PRs can't run `build-*` jobs | No repo secrets available to fork-triggered runs; standard GitHub Actions limitation | part-03 (F-17) |
| Cosign signing is write-only — nothing verifies at pull time | Real fix is an admission policy; out of scope for this engagement | part-03 (F-10) |
| Cached downstream response can outlive a rollback for up to `CACHE_TTL_SECONDS` (300s) | `redis-cli FLUSHALL` is the documented optional rollback step | deploy/README.md |
| Argo CD installed imperatively, not itself GitOps-managed | App-of-apps is the standard fix; deliberately out of scope | part-03 |
| Argo CD admin password printed to console at bootstrap | Fine for a laptop stand-in, needs a real secret-handling decision before any real deployment | part-03 |
| E2E Job uses the real HMAC signing key, not a dedicated test credential | Blocked on a protocol change (see M3 below); blast radius is a Job in a namespace that already holds the Secret | part-03 |
| Gitignored sealing key with no backup enforcement | KMS-backed key management is the real production answer; out of scope for this engagement | `docs/issues/023` |
| GHCR has no retention policy | Unlimited image accumulation; fine at this scale | part-03 |
| `mock_service.received_pokemon` grows unbounded in-process | Fine at assignment scale; bound it or trim on read for anything long-lived | part-03 |
| Argo polls on a shortened interval rather than event-driven sync | Simpler for a take-home; a webhook-driven sync is the real fix | part-03 |
| Cold start serializes image builds behind the full monitoring install (~5 min) | Needs a CRD-only fast dependency split from the `--wait` install; wall-clock only, not correctness | part-05 (A-10) |
| `make down` leaves built images behind (~3.5GB/cycle) | Correct today — layer-cache reuse is the point; needs a product decision (document vs. an opt-in `make clean-images`) | part-05 (A-11) |
| Tilt extensions fetch unpinned from GitHub at Tiltfile-load time | Needs research into whether Tilt's extension-loading syntax supports version/commit pinning at all | part-05 (A-13) |
| No replay protection on signed payloads (HMAC covers body only) | Protocol change — every legitimate client, including the load generator, would need to change what it signs. Not a decision this project can make unilaterally | documented only |
| A slow-trickle downstream can outlive `FORWARD_DEADLINE_SECONDS` within a single attempt | `httpx`'s `read` timeout bounds the gap *between* socket reads, not the total time to drain a response, and the deadline is only checked between attempts. A downstream dripping bytes just under the read timeout holds one attempt open indefinitely. Same semantics apply to the inbound body reader, so this is not new. Closing it means wrapping both read loops in `asyncio.wait_for(remaining_deadline)`; deliberately not done in the final review pass because it changes timeout behaviour on the request path and the existing tests only cover the fully-hung case | `docs/issues/030` |
