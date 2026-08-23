# S4 — `values-prod.yaml` was undeployable on two independent counts

**Severity:** Should fix · **Part:** 2 audit (2026-08-23), closed in Part 3 step 4a · **Status:** Fixed
**Files:** `deploy/helm/pokeproxy/values-prod.yaml` (deleted), `deploy/envs/prod/values.yaml` (new)

## Problem

`values-prod.yaml` was written during Part 2 as the intended production values file, but it lints and renders clean while describing a deployment that cannot actually run:

1. It sets `components.mock-downstream.enabled: false`, but the routing rules (`components.pokeproxy.rules`) still resolve their forward URL to the mock's Service address — the chart has no other URL source for a rule without an explicit `url:` override. The app would start, accept traffic, and fail every forward against a Service that was never created.
2. `allow-pokeproxy-egress-to-dependencies`, the only egress NetworkPolicy rule scoped to the pokeproxy pod, permits traffic to in-namespace Redis and mock-downstream pods only. With `mock-downstream` disabled and no real downstream in-cluster, `default-deny-all` blocks egress to any external destination. Even a hand-fixed rules file pointing at a real external URL would still be silently dropped at the network layer.

Neither defect is visible from `helm lint --strict` or `helm template | kubeconform` — both validate schema and templating, not whether the rendered rules and NetworkPolicy are mutually satisfiable at runtime. Found by re-reading the rendered manifests against each other during the Part 2 completion audit, not by any automated check.

## Production Impact

Anyone deploying from this file gets a cluster that reports Healthy (pods Ready, probes passing) while silently dropping 100% of matched traffic — the worst kind of failure, since nothing in the Kubernetes-visible state indicates a problem. A real production rollout from this file would look identical to a working one in `kubectl get pods` and only surface in application-level metrics or a downstream partner reporting missing data.

## Options Considered

- **Hand-fix `values-prod.yaml` in place**: add real downstream URLs to the rules and widen the NetworkPolicy. Rejected — there is no real downstream to point at in this assignment, so "fixing" it would mean inventing a fictional URL, which documents nothing real and can't be verified live.
- **Delete it and defer prod entirely to later**: leaves S4 open and blocks Part 3's GitOps demonstration, which needs *some* real, deployable prod-shaped environment.
- **Supersede it with an environment that actually exists**: build the prod stand-in (a second k3d cluster, `mock-downstream` enabled, Argo CD, GHCR-published digests) that Part 3 needed anyway, and let its values file be the "real" prod values by construction.

## Decision

Deleted `values-prod.yaml` outright rather than repairing it. Part 3 step 4a introduces `deploy/envs/prod/values.yaml` as its replacement, describing an environment that is actually stood up and deployed (`pokeproxy-prod` cluster, `mock-downstream.enabled: true`, Traefik ingress, GHCR digest-pinned images) rather than an aspirational one. The external-downstream delta — what would differ for a *real* production target — is written down as prose in `deploy/README.md`'s "Delta from a real production" table instead of encoded in a values file nobody can verify.

## Implementation

`git rm deploy/helm/pokeproxy/values-prod.yaml`; new `deploy/envs/prod/values.yaml` created as part of the Part 3 step 4a `deploy/envs/` restructuring (see `docs/issues/022-seal-hmac-wholesale-rewrite.md` for the sibling fix in the same commit). CI's `chart-lint` job gained a second `helm lint`/`kubeconform` pass against this new file.

## Verification

| Check | Result |
|---|---|
| `helm template` against `deploy/envs/prod/values.yaml` | 24 resources render, digest-pinned image references confirmed in output |
| Deployed to a real cluster (`pokeproxy-prod`, port 8081) | Synced/Healthy via Argo CD, 4/4 pods, 0 restarts |
| PostSync E2E against this values file | Passed — real protobuf delivered to the real (enabled) mock-downstream, confirmed via `/received` |
| NetworkPolicy egress in this configuration | Not exercised against a real external downstream (none exists for this assignment) — the in-cluster mock path is proven live; the external-egress rule remains the documented delta in `deploy/README.md` |

## Tradeoffs / Remaining Risk

This closes S4 for the environment that actually exists in this assignment. It does **not** prove the NetworkPolicy egress rule works against a genuine external downstream, since none is available to test against — that gap is named explicitly in `deploy/README.md` rather than papered over. Whoever stands up a real production deployment from this chart needs to add real rule URLs and widen `allow-pokeproxy-egress-to-dependencies` before disabling `mock-downstream` — recorded in `WORKLOG.md`'s Part 3 backlog.
