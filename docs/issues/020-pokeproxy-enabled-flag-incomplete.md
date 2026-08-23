# `components.pokeproxy.enabled: false` still rendered five pokeproxy resources

## Problem

An earlier fix (S1) gated `pokeproxy/deployment.yaml` and `pokeproxy/service.yaml` on `$spec.enabled`, matching the pattern already applied to mock-downstream. It stopped there. Five other templates owned by the pokeproxy component had no gate at all:

`helm template --set components.pokeproxy.enabled=false` still emitted:

| Resource | Template |
|---|---|
| `ConfigMap <release>-env` | `pokeproxy/configmap-env.yaml` |
| `ConfigMap <release>-rules` | `pokeproxy/configmap-rules.yaml` |
| `SealedSecret <release>-hmac` | `pokeproxy/sealedsecret-hmac.yaml` |
| `Ingress <release>` | `pokeproxy/ingress.yaml` (gated on `ingress.enabled` only) |
| `Middleware body-limit` | `pokeproxy/traefik-middleware.yaml` (same) |

Four of the five are inert clutter. The Ingress is not: it routes `/stream` to `service/<release>`, which S1's fix now correctly declines to create.

## Production Impact

The flag means the opposite of what it says. Setting `components.pokeproxy.enabled: false` was supposed to mean "this component is not deployed here" — instead the cluster keeps a public route on `/stream` that resolves to no backend, so Traefik answers **503** rather than the caller getting nothing at all.

That is worse than either honest outcome. A 503 from a live route reads as "the service is deployed and broken" — it will be probed by uptime checks, alerted on, and debugged as an outage — when the truth is "the service was intentionally not installed." The realistic path here is a values file that disables pokeproxy to deploy only Redis and the mock for a dependency-test environment, and then spends an afternoon explaining a 503.

The orphaned `SealedSecret` is a smaller version of the same: the controller decrypts it and materialises a `Secret` holding the HMAC key into a namespace where nothing consumes it.

## Options Considered

| Option | Verdict |
|---|---|
| Leave it, document the flag as pokeproxy-Deployment-only | Rejected — a flag that half-works is worse than no flag |
| Delete the `enabled` key for pokeproxy since nobody disables it | Rejected for the same reason S1 rejected it: `serviceaccount.yaml` reads `enabled` generically in a `range` over every component, so removing the key silently breaks ServiceAccount creation through Helm's nil-is-falsy behavior |
| Gate the remaining five templates the same way | **Chosen** |

## Decision

Wrap all five on the same condition. Two forms, matching what each file already does:

- `configmap-env.yaml`, `configmap-rules.yaml`, `sealedsecret-hmac.yaml` — a top-level `{{- if .Values.components.pokeproxy.enabled }} … {{- end }}`.
- `ingress.yaml`, `traefik-middleware.yaml` — added to the existing `and` condition rather than nesting a second `if`.

The four pokeproxy-related NetworkPolicies are **deliberately left ungated**. Unlike the Ingress they are provably inert when pokeproxy is absent: `allow-ingress-to-pokeproxy` and `allow-pokeproxy-egress-to-dependencies` select a pod that does not exist, and `allow-pokeproxy-to-redis` / `allow-pokeproxy-to-mock-downstream` select redis and mock but permit ingress *from* a selector matching nothing — which leaves those two exactly as locked down as `default-deny-all` already makes them. Gating them would thread a per-component flag into a file whose own switch is `networkPolicy.enabled`, mixing two concerns to remove output nobody reads.

## Implementation

Five one-line conditions plus three `{{- end }}`. No logic moved.

One subtlety worth recording: the obvious placement broke the pod-template checksum. Putting `{{- if … }}` *after* the two `{{- $var := … -}}` assignments in `configmap-env.yaml` left the `}}` of the `if` emitting a newline before `apiVersion:`, because no `{{-` followed it to strip it. The ConfigMap's rendered bytes changed, so `include … | sha256sum` changed, so `checksum/config-env` changed, so **every pokeproxy pod would have rolled on the next upgrade for a whitespace edit**. Moving the `if` above the assignments fixes it — the assignments start with `{{-` and strip the newline themselves. Caught by diffing renders, not by reading.

## Verification

| Check | Result |
|---|---|
| Render before vs after, `values-local.yaml` | **Byte-identical** |
| Render before vs after, `values-prod.yaml` | **Byte-identical** |
| `--set components.pokeproxy.enabled=false`, before | 17 resources incl. `ConfigMap`×2, `SealedSecret`, `Ingress`, `Middleware` |
| `--set components.pokeproxy.enabled=false`, after | **12 resources** — all five gone; mock, redis and the NetworkPolicies remain |
| All three components disabled | 6 NetworkPolicies, valid YAML, no dangling references |
| `helm lint`, both values files | Clean |
| Live redeploy on the running cluster | Revision 2, 4/4 Ready, **`checksum/config-{env,rules}` unchanged and no pod rolled** (pod ages unchanged across the upgrade) |
| E2E through the real ingress after | **11/11 checks pass** |
| App suite | `pytest -q` 106 passed, `ruff` clean |

The "before" renders came from `git show HEAD:` copies of the five templates in a scratch chart, so the comparison is against the committed tree rather than memory.

## Tradeoffs / Remaining Risk

The four NetworkPolicies still render when pokeproxy is disabled. Argued above as inert; the cost is a slightly noisier `kubectl get netpol`.

Disabling pokeproxy while leaving `redis.enabled: true` still produces a Redis instance nothing talks to. That is a coherent thing to want (dependency-only environment), so it is not treated as an error.

`values-prod.yaml` remains undeployable for unrelated reasons tracked as S4 — rules pointing at the mock Service it disables, and a NetworkPolicy egress allowlist with no entry for a real external downstream. Untouched by this change.
