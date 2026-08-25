#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CLUSTER_NAME="pokeproxy"
KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-$CLUSTER_NAME}"

if ! k3d cluster list "$CLUSTER_NAME" >/dev/null 2>&1; then
  echo "k3d cluster '$CLUSTER_NAME' does not exist, nothing to tear down"
  exit 0
fi

cd "$REPO_ROOT"

echo "==> tilt down"
tilt down --context "$KUBE_CONTEXT" || echo "tilt down reported an issue -- proceeding to delete the cluster anyway, which removes everything regardless"

echo "==> Deleting k3d cluster '$CLUSTER_NAME'"
k3d cluster delete "$CLUSTER_NAME"
