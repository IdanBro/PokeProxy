# H6 — Rules, config path, and mock service all assumed a single laptop

**Severity:** High · **Part:** 2, steps 1, 3, 5 · **Status:** Fixed
**Files:** `app/config/rules.json`, `app/Dockerfile`, `app/Dockerfile.mock`, `app/mock_service/main.py`, `deploy/helm/pokeproxy/templates/pokeproxy/configmap-rules.yaml`

## Problem

Three separate places assumed the whole stack ran as sibling processes on one machine, all reachable via `localhost`:

- `config/rules.json` hardcoded every rule's forwarding URL as `http://localhost:8001/pokemon`.
- `Settings.pokeproxy_config` defaulted to the relative path `config/rules.json`, resolved against whatever the process's current working directory happened to be (the same class of bug M7 already fixed for the app's own CWD-dependence — this was the same assumption, just in the config value itself).
- `mock_service/main.py`'s only entrypoint bound `host="127.0.0.1"` — unreachable from anywhere but the same network namespace.

None of it survives a container boundary, let alone a Kubernetes network.

## Production Impact

Deployed as-is, PokeProxy would successfully start, successfully validate its rules file, and then fail every single forward with a connection error — because `localhost:8001` inside the `pokeproxy` container is the `pokeproxy` container, not the `mock-downstream` (or real downstream) container next to it. The failure mode is a silent 502/504 on every matching request, not a startup crash, so it would only surface once real traffic hit a matching rule.

## Options Considered

| Decision | Options | Chosen |
|---|---|---|
| Where rules live | keep `rules.json` as a file baked into the image · externalize via ConfigMap · template from Helm values | **Helm values, rendered to a ConfigMap** |
| How the downstream URL is computed | hardcode the cluster-internal Service DNS name as a literal string in values · derive it from the same naming helper that creates the Service | **derive it from the naming helper** |
| Config path | keep relative, rely on `WORKDIR` matching · make it absolute in the image | **absolute (`/etc/pokeproxy/rules.json`)** |
| Mock service bind address | keep `127.0.0.1`, rely on port-forwarding · bind `0.0.0.0` | **`0.0.0.0`**, and the dead single-purpose entrypoint that hardcoded it was deleted entirely (see issue 014) |

## Decision

Rules move into `values.yaml` as `{reason, match}` pairs with no `url` field at all. The chart's `configmap-rules.yaml` template computes the downstream URL once, using the exact same `pokeproxy.component.fullname` helper that names the `mock-downstream` Service, and merges it into every rule before rendering to JSON. The URL literally cannot drift from the Service that actually exists, because both are derived from the same source rather than typed twice.

`POKEPROXY_CONFIG` gets a new absolute default baked into `app/Dockerfile` (`/etc/pokeproxy/rules.json`), matching exactly where the rules ConfigMap gets volume-mounted — no relative-path resolution left anywhere in the container.

`mock_service`'s new `Dockerfile.mock` (issue 014) runs `uvicorn --host 0.0.0.0`.

## Implementation

`deploy/helm/pokeproxy/templates/pokeproxy/configmap-rules.yaml`:
```
{{- $mockName := include "pokeproxy.component.fullname" (dict "context" $ "component" "mock-downstream") -}}
{{- $downstreamURL := printf "http://%s.%s.svc.cluster.local.:%v/pokemon" $mockName .Release.Namespace $mockSpec.port -}}
```
computed once, merged into each `values.yaml` rule via Sprig's `merge`, rendered with `toJson`.

Fully-qualified with the trailing dot (`....svc.cluster.local.`) — `ndots:5` in the default pod `resolv.conf` makes short names walk the DNS search list; the trailing dot short-circuits that.

## Verification

| Check | Result |
|---|---|
| Rendered `rules.json` piped through the app's real `pokeproxy.rules.load_rules()` | Parses into the identical 3 `Rule` objects the local `config/rules.json` produces, URL correctly swapped to `http://pokeproxy-mock-downstream.pokeproxy.svc.cluster.local.:8001/pokemon` |
| Live cluster, signed request through a real forward | `200 {"status":"received"}`, confirmed landing in mock-downstream's `/received` with the correct `reason` — proves DNS resolution and routing, not just template rendering |
| `POKEPROXY_CONFIG` absolute path vs. ConfigMap mount path | Cross-checked directly: Dockerfile `ENV`, volume `mountPath`, and ConfigMap key all agree on `/etc/pokeproxy/rules.json`, with no `subPath` in between to introduce a mismatch |

## Tradeoffs / Remaining Risk

| Item | Disposition |
|---|---|
| Rules now live in two places — `app/config/rules.json` (local dev) and `values.yaml` (cluster) | Accepted. The alternative (mounting the local file via `.Files.Get`) loses the per-environment values story `values-prod.yaml` needs |
| A rules ConfigMap change is inert until the pods restart | Solved separately — `checksum/config-rules` pod-template annotation, live-verified in step 9 to actually trigger a rollout |
