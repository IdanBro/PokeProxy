#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-pokeproxy}"

echo "Restoring the real rules.json (Part 3 scenario B recovery, on demand)"

helm upgrade --install pokeproxy "$REPO_ROOT/deploy/helm/pokeproxy" \
  --kube-context "$KUBE_CONTEXT" \
  --namespace pokeproxy \
  --reuse-values \
  -f "$REPO_ROOT/deploy/envs/local/values.good-rules.yaml" \
  --atomic --timeout=4m
