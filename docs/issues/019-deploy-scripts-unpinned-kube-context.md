# Deploy scripts never pinned a kube context and could target the wrong cluster

## Problem

`scripts/deploy.sh` and `scripts/seal-hmac.sh` issued every `kubectl` and `helm` call without naming a context, so both followed whatever `kubectl config current-context` happened to be.

The cluster guard did not protect against this. `k3d cluster list pokeproxy` reads Docker, not kubeconfig — measured exit `0` under both `k3d-pokeproxy` and `docker-desktop`, and `1` only for a cluster name that does not exist. So on the reuse path (`scripts/deploy.sh:39`) the script correctly finds the k3d cluster whatever context is current, then **skips `k3d cluster create` — the only step that would have switched the context** — and proceeds.

Everything after that followed the wrong cluster:

| Step | Old call | What it targeted |
|---|---|---|
| 3 | `kubectl apply -f deploy/k8s/namespace.yaml` | current context |
| 4 → `seal-hmac.sh:62` | `kubectl apply -f .secrets/sealing-key.yaml` | current context — an **RSA private key into `kube-system`** |
| 4 → `seal-hmac.sh:67` | `helm upgrade --install sealed-secrets` | current context |
| 4 → `seal-hmac.sh:89` | `kubeseal --raw` | current context |
| 5 | `helm upgrade --install pokeproxy` | current context |

Reproduced rather than argued. `kubectl config get-contexts` on this machine lists two contexts. With `docker-desktop` current, the old step-3 command:

```
error validating "deploy/k8s/namespace.yaml": failed to download openapi:
Get "https://kubernetes.docker.internal:6443/openapi/v2": dial tcp 127.0.0.1:6443: connect: connection refused
```

It went to `docker-desktop`, exactly as feared. It failed only because Docker Desktop's Kubernetes is switched off — that is luck, not design.

## Production Impact

On any machine with a second *reachable* context — a shared dev cluster, a staging kubeconfig, a colleague's `minikube` — running `deploy.sh` installs PokeProxy somewhere it was never meant to go, and does it silently, because every command succeeds.

The sealing key is the sharp edge. `seal-hmac.sh` applies the RSA **private** key into `kube-system` before anything else runs, so a mis-targeted run leaks this project's sealing key into an unrelated cluster. Nothing in the output would say so.

This also lands directly on Part 5: a one-command bootstrap that quietly depends on ambient shell state is not idempotent in any useful sense, and CI runners in particular have no reason to have the right context selected.

## Options Considered

| Option | Verdict |
|---|---|
| `kubectl config use-context k3d-pokeproxy` at the top of the script | Rejected — mutates the user's shell state as a side effect of a deploy. A script should not repoint someone's `kubectl` |
| Hardcode `--context k3d-pokeproxy` everywhere | Close, but unusable if someone renames the cluster, and duplicates the literal in two files |
| Derive the context from the cluster name, overridable by env var, and pass it explicitly on every call | **Chosen** |

## Decision

`KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-$CLUSTER_NAME}"` in both scripts, threaded explicitly onto every cluster-touching call. `deploy.sh` exports it to `seal-hmac.sh`, so the two cannot disagree; `seal-hmac.sh` still defaults sanely when run standalone.

Both scripts then assert the context actually exists before doing anything, and exit `1` naming it if not. That converts the one real regression risk of this change — a renamed cluster — from a confusing mid-run failure into a one-line message at the top.

Explicit flags beat `use-context` because the script becomes independent of ambient state rather than overwriting it: after a run, the operator's own context is exactly where they left it.

## Implementation

- `scripts/deploy.sh`: `KUBE_CONTEXT` variable; existence check after the cluster step; `--context` on the namespace apply and the verify `get pods`; `--kube-context` on `helm upgrade`; `KUBE_CONTEXT` exported into the `seal-hmac.sh` call.
- `scripts/seal-hmac.sh`: same variable and existence check; `--context` on the key apply and the `rollout status`; `--kube-context` on the controller install; `--context` on `kubeseal --raw` (supported, kubeseal 0.39.1).

`k3d image import` and the `kubectl ... --dry-run=client` / `kubectl label --local` calls are deliberately untouched — the first addresses the cluster by name through Docker, the other two never contact an API server.

## Verification

| Check | Result |
|---|---|
| `bash -n` both scripts | Clean |
| `deploy.sh` with a nonexistent context | `Kube context 'does-not-exist' not found…`, **exit 1**, before any image build |
| `seal-hmac.sh` standalone, nonexistent context | Same message, **exit 1** |
| Old behavior reproduced | Bare `kubectl apply -f deploy/k8s/namespace.yaml` under `docker-desktop` → `dial tcp 127.0.0.1:6443: connect: connection refused` |
| **`deploy.sh` run with `current-context = docker-desktop`** | **Deployed to `k3d-pokeproxy` anyway** — `Sealing against kube context 'k3d-pokeproxy'`, `namespace/pokeproxy unchanged`, revision 2, 4/4 Ready, ingress probe 401, 1m15s |
| Operator's context after that run | Still `docker-desktop` — the script never mutated it |
| E2E through the real ingress afterwards | **11/11 checks pass** |
| App suite | `pytest -q` 106 passed, `ruff` clean |

## Tradeoffs / Remaining Risk

The default `k3d-pokeproxy` is derived from `CLUSTER_NAME`, which is duplicated in both scripts. If someone changes the cluster name they must change it in both, or set `KUBE_CONTEXT`. Acceptable for two scripts; if a third appears, the pair belongs in a shared `scripts/lib.sh`.

`k3d cluster list` still reads Docker rather than kubeconfig, so the script can still find a cluster whose context has been deleted from kubeconfig. That case is now caught loudly by the existence check instead of misfiring.

Nothing here validates that the named context *points at the k3d cluster* — someone who renames a context to `k3d-pokeproxy` while pointing it elsewhere still wins. Not worth defending against; the failure mode this fixes was accidental, not adversarial.
