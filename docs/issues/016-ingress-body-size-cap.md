# M2 (ingress half) — Edge-layer defense-in-depth for the unbounded body-buffering gap

**Severity:** Medium (partial — this closes only the ingress half) · **Part:** 2, step 8 · **Status:** Fixed
**Files:** `deploy/helm/pokeproxy/templates/pokeproxy/{ingress.yaml,traefik-middleware.yaml}`

## Problem

`docs/issues/000-known-gaps.md` already documents the app-level half of M2 as found, root-caused, and deliberately not fixed: `proxy.py`'s `stream()` calls `await request.body()`, which fully buffers the request into memory *before* the `len(body) > MAX_BODY_SIZE` check ever runs. A client that omits or lies about `Content-Length` gets its entire payload buffered regardless of size — a real resource-exhaustion vector, not a cosmetic one. That write-up explicitly named "achievable at the Part 2 ingress layer as defense-in-depth" as the reason the app-level fix could reasonably wait. This issue is that ingress-layer half actually landing.

## Production Impact

Unchanged from the original finding for the app-level gap — this issue doesn't reduce that risk, it adds a second, independent layer in front of it. The production value here is specifically in **defense-in-depth**: if the app-level check is ever bypassed, has a regression, or simply hasn't been implemented yet (as is the case today), an oversized request still gets rejected before it reaches the application process at all, at the point where rejecting it is cheapest.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Where the cap lives | app-level fix only (already scoped, not yet implemented) · ingress-level cap only · both | **ingress-level now, app-level fix stays separately scoped** — neither replaces the other |
| Ingress technology | Traefik `Middleware` CRD (k3d's bundled ingress controller) · ingress-nginx annotation | **Traefik**, since that's what's actually running; kept swappable via a values flag for `values-prod.yaml` |
| Cap value | match the app's existing `MAX_BODY_SIZE` (1 MiB) exactly · pick an independent threshold | **1 MiB**, matching exactly — one number to reason about, not two that could drift apart |

## Decision

A Traefik `Middleware` (`buffering.maxRequestBodyBytes: 1048576`) referenced from the `Ingress` resource via the `traefik.ingress.kubernetes.io/router.middlewares` annotation. `values.yaml`'s `ingress.bodyLimit.provider` switch (`traefik` | `nginx`) keeps the mechanism swappable — a future `values-prod.yaml` running behind ingress-nginx would instead render the equivalent `nginx.ingress.kubernetes.io/proxy-body-size` annotation, unexercised in this cluster but wired and ready.

## Implementation

`templates/pokeproxy/traefik-middleware.yaml`:
```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
spec:
  buffering:
    maxRequestBodyBytes: {{ .Values.ingress.bodyLimit.maxBytes | int }}
```
The `| int` cast is load-bearing, not defensive styling: Helm decodes YAML numbers as `float64` internally, and Go's default float formatting renders round values like `1048576.0` in scientific notation (`1.048576e+06`). Caught before the cluster ever saw it, by inspecting the rendered template rather than trusting it.

`templates/pokeproxy/ingress.yaml` references the middleware and exposes only `path: /stream`, `pathType: Exact` — the same "expose nothing but the one real endpoint" decision that gives `/health`/`/ready`/`/stats` a free M5 mitigation.

## Verification

| Check | Result |
|---|---|
| >1 MiB request through the real ingress | `413`, body is Traefik's plain-text `Request Entity Too Large` — **not** the app's `{"error": "payload too large"}` JSON |
| App access log for that request | **Zero trace** — the request never reached the pokeproxy pod. Without this check, a bare `413` would have been ambiguous between "the new Middleware caught it" and "the app's pre-existing (buggy) check caught it after all," proving nothing about the new layer |
| `maxRequestBodyBytes` rendering | Confirmed as a clean integer (`1048576`) after the `\| int` fix, not `1.048576e+06` |
| `helm lint --strict` | clean |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| The app-level streaming fix (`request.stream()`, count-and-abort before finishing the read) remains unimplemented | Unchanged from `docs/issues/000-known-gaps.md` — this issue does not close that gap, it adds a layer in front of it. Still fully scoped, ready to pick up |
| `values-prod.yaml`'s ingress-nginx branch is unexercised | No ingress-nginx cluster exists to test against; defined-not-demonstrated, consistent with the rest of `values-prod.yaml` |
