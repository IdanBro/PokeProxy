#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-pokeproxy}"

echo "Deploying deliberately broken rules.json (Part 3 scenario B, on demand)"

helm upgrade --install pokeproxy "$REPO_ROOT/deploy/helm/pokeproxy" \
  --kube-context "$KUBE_CONTEXT" \
  --namespace pokeproxy \
  --reuse-values \
  -f "$REPO_ROOT/deploy/envs/local/values.broken-rules.yaml" \
  --atomic --timeout=4m

echo "If this line is reached, --atomic did NOT roll back -- that's unexpected, investigate."
