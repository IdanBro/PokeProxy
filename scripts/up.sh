#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE="${1:-ci}"
case "$MODE" in
  ci|dev) ;;
  *)
    echo "Usage: $0 [ci|dev]" >&2
    exit 1
    ;;
esac

CLUSTER_NAME="pokeproxy"
CLUSTER_CONFIG="$REPO_ROOT/deploy/k3d/cluster.yaml"
KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-$CLUSTER_NAME}"

bash "$REPO_ROOT/scripts/preflight.sh" --env local

echo "==> Cluster"
if k3d cluster list "$CLUSTER_NAME" >/dev/null 2>&1; then
  echo "k3d cluster '$CLUSTER_NAME' already exists, reusing it"
else
  echo "Creating k3d cluster '$CLUSTER_NAME'"
  k3d cluster create --config "$CLUSTER_CONFIG"
fi

kubectl config get-contexts "$KUBE_CONTEXT" >/dev/null 2>&1 || {
  echo "Kube context '$KUBE_CONTEXT' not found. k3d writes it when it creates the cluster." >&2
  echo "If you renamed it, re-run with KUBE_CONTEXT=<name>." >&2
  exit 1
}

cd "$REPO_ROOT"

if [[ "$MODE" == "dev" ]]; then
  echo "==> tilt up (interactive)"
  exec tilt up
else
  echo "==> tilt ci"
  exec tilt ci
fi
