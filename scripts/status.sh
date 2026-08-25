#!/usr/bin/env bash
# No -e: this is a read-only status report, and one failing probe should not
# hide the rest of the picture.
set -uo pipefail

CLUSTER_NAME="pokeproxy"
KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-$CLUSTER_NAME}"

if ! k3d cluster list "$CLUSTER_NAME" >/dev/null 2>&1; then
  echo "k3d cluster '$CLUSTER_NAME' does not exist. Run 'make up' or 'make dev' to create it."
  exit 0
fi

echo "== k3d cluster =="
k3d cluster list "$CLUSTER_NAME"

echo
echo "== pods (pokeproxy) =="
kubectl --context "$KUBE_CONTEXT" get pods -n pokeproxy 2>&1

echo
echo "== helm releases =="
helm list -A --kube-context "$KUBE_CONTEXT" 2>&1

echo
echo "== ingress probe (POST /stream, no signature -- expect 401) =="
status="$(curl -s -o /dev/null -w '%{http_code}' -X POST http://localhost:8080/stream || echo "000")"
echo "http_code=$status"
