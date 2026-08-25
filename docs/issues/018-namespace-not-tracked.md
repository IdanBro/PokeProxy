# B2 — Namespace and its PodSecurity enforcement existed nowhere in git

**Severity:** Blocker · **Part:** 2 audit (2026-08-23) · **Status:** Fixed
**Files:** `deploy/k8s/namespace.yaml` (new)

## Problem

Step 6 correctly found that Helm requires the target namespace to exist before applying anything (`Error: create: failed to create: namespaces "pokeproxy" not found`), and that `--create-namespace` collides with a chart-owned `Namespace` resource on ownership metadata. The fix — removing `templates/namespace.yaml` from the chart and creating the namespace declaratively outside the Helm release — was correct. But the replacement was only ever a `kubectl create namespace | kubectl label --local | kubectl apply` command recorded in prose, in the planning doc. `git grep pod-security` matched nothing but those two markdown files — no manifest, no script.

The live `pokeproxy` namespace carries `pod-security.kubernetes.io/{enforce,audit,warn}: restricted`, which is what actually rejects privileged/root pods (verified in the Part 2 audit: a `privileged: true, runAsUser: 0` test pod was refused with `violates PodSecurity "restricted:latest"`, naming all six violations). That enforcement exists only because someone typed the right command by hand, once, on this machine.

## Production Impact

A clone-and-deploy either fails outright at `namespaces "pokeproxy" not found` (no docs point at a fix), or someone works around it with a bare `kubectl create namespace pokeproxy` / `helm install --create-namespace` — both of which create the namespace **without** the PSA labels. Everything still deploys and looks healthy; the difference is invisible until someone (or something malicious) actually ships a privileged or root container, which the restricted PSA level exists specifically to block. A security control silently not existing is worse than one that's visibly absent, because nothing signals the gap.

## Solution

Committed `deploy/k8s/namespace.yaml` — the exact manifest the step-6 command already produced, captured as a file instead of prose. Applying it (`kubectl apply -f deploy/k8s/namespace.yaml`) before `helm upgrade --install` is now a scriptable, idempotent step Part 5's bootstrap can run directly, rather than a hand-typed one-liner nobody else can reproduce from the repo alone.

## Verification

| Check | Result |
|---|---|
| Apply against the already-existing live namespace | `namespace/pokeproxy configured` — no-op; `kubectl get ns pokeproxy` labels unchanged (byte-identical to before) |
| Apply against a namespace that doesn't exist yet (renamed copy, `psa-dryrun-test`) | `namespace/psa-dryrun-test created` with all five labels present, confirming the file works standalone on a cluster that has never seen this namespace |
| Cleanup | `psa-dryrun-test` deleted |
