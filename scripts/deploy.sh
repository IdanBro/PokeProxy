#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CLUSTER_NAME="pokeproxy"
KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-$CLUSTER_NAME}"
CLUSTER_CONFIG="$REPO_ROOT/deploy/k3d/cluster.yaml"
NAMESPACE_MANIFEST="$REPO_ROOT/deploy/k8s/namespace.yaml"
CHART_DIR="$REPO_ROOT/deploy/helm/pokeproxy"
VALUES_LOCAL="$CHART_DIR/values-local.yaml"
SEAL_SCRIPT="$REPO_ROOT/scripts/seal-hmac.sh"
APP_DIR="$REPO_ROOT/app"

APP_NAMESPACE="pokeproxy"
RELEASE_NAME="pokeproxy"
INGRESS_URL="http://localhost:8080/stream"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_command docker
require_command kubectl
require_command helm
require_command k3d
require_command kubeseal
require_command openssl
require_command git

docker info >/dev/null 2>&1 || {
  echo "Docker doesn't seem to be running. Start Docker Desktop and re-run." >&2
  exit 1
}

echo "==> 1. Cluster"
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
echo "Deploying to kube context '$KUBE_CONTEXT'"

echo "==> 2. Build and import images"
GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
echo "Building at sha $GIT_SHA"
docker build --build-arg GIT_SHA="$GIT_SHA" -t "pokeproxy:$GIT_SHA" -f "$APP_DIR/Dockerfile" "$APP_DIR"
docker build --build-arg GIT_SHA="$GIT_SHA" -t "mock-downstream:$GIT_SHA" -f "$APP_DIR/Dockerfile.mock" "$APP_DIR"
k3d image import "pokeproxy:$GIT_SHA" "mock-downstream:$GIT_SHA" -c "$CLUSTER_NAME"

echo "==> 3. Namespace"
kubectl --context "$KUBE_CONTEXT" apply -f "$NAMESPACE_MANIFEST"

echo "==> 4. Seal the HMAC secret"
KUBE_CONTEXT="$KUBE_CONTEXT" bash "$SEAL_SCRIPT"

echo "==> 5. Deploy"
helm upgrade --install "$RELEASE_NAME" "$CHART_DIR" \
  --kube-context "$KUBE_CONTEXT" \
  -n "$APP_NAMESPACE" \
  -f "$VALUES_LOCAL" \
  --set components.pokeproxy.image.tag="$GIT_SHA" \
  --set components.mock-downstream.image.tag="$GIT_SHA" \
  --atomic --timeout 3m

echo "==> 6. Verify"
kubectl --context "$KUBE_CONTEXT" get pods -n "$APP_NAMESPACE"

echo "Probing $INGRESS_URL (expect 401 — POST with no signature)"
status="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$INGRESS_URL" || echo "000")"
if [[ "$status" == "401" ]]; then
  echo "Ingress reachable, HMAC validation active (401 as expected)"
else
  echo "Unexpected status from $INGRESS_URL: $status" >&2
  exit 1
fi

echo "Done. PokeProxy is deployed and reachable at $INGRESS_URL"
