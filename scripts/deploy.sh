#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CLUSTER_NAME="pokeproxy"
KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-$CLUSTER_NAME}"
CLUSTER_CONFIG="$REPO_ROOT/deploy/k3d/cluster.yaml"
NAMESPACE_MANIFEST="$REPO_ROOT/deploy/k8s/namespace.yaml"
CHART_DIR="$REPO_ROOT/deploy/helm/pokeproxy"
VALUES_LOCAL="$REPO_ROOT/deploy/envs/local/values.yaml"
SEAL_SCRIPT="$REPO_ROOT/scripts/seal-hmac.sh"
APP_DIR="$REPO_ROOT/app"

APP_NAMESPACE="pokeproxy"
RELEASE_NAME="pokeproxy"
INGRESS_URL="http://localhost:8080/stream"

MONITORING="${MONITORING:-true}"
MONITORING_NAMESPACE="monitoring"
MONITORING_NAMESPACE_MANIFEST="$REPO_ROOT/deploy/k8s/namespace-monitoring.yaml"
MONITORING_RELEASE="kube-prometheus-stack"
MONITORING_CHART_VERSION="88.5.4"
MONITORING_VALUES="$REPO_ROOT/deploy/monitoring/values.yaml"

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
docker build --build-arg BASE_IMAGE="pokeproxy:$GIT_SHA" -t "pokeproxy-e2e:$GIT_SHA" -f "$APP_DIR/Dockerfile.e2e" "$APP_DIR"
k3d image import "pokeproxy:$GIT_SHA" "mock-downstream:$GIT_SHA" "pokeproxy-e2e:$GIT_SHA" -c "$CLUSTER_NAME"

echo "==> 3. Namespace"
kubectl --context "$KUBE_CONTEXT" apply -f "$NAMESPACE_MANIFEST"

echo "==> 4. Seal the HMAC secret"
KUBE_CONTEXT="$KUBE_CONTEXT" bash "$SEAL_SCRIPT" --env local

echo "==> 5. Deploy"
helm upgrade --install "$RELEASE_NAME" "$CHART_DIR" \
  --kube-context "$KUBE_CONTEXT" \
  -n "$APP_NAMESPACE" \
  -f "$VALUES_LOCAL" \
  --set components.pokeproxy.image.tag="$GIT_SHA" \
  --set components.mock-downstream.image.tag="$GIT_SHA" \
  --set e2e.enabled=true \
  --set e2e.image.tag="$GIT_SHA" \
  --atomic --timeout 3m

echo "==> 6. Monitoring stack (kube-prometheus-stack)"
if [[ "$MONITORING" == "false" ]]; then
  echo "MONITORING=false, skipping monitoring stack install"
else
  kubectl --context "$KUBE_CONTEXT" apply -f "$MONITORING_NAMESPACE_MANIFEST"
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
  helm repo update prometheus-community >/dev/null
  helm upgrade --install "$MONITORING_RELEASE" prometheus-community/kube-prometheus-stack \
    --kube-context "$KUBE_CONTEXT" \
    --version "$MONITORING_CHART_VERSION" \
    --namespace "$MONITORING_NAMESPACE" \
    -f "$MONITORING_VALUES" \
    --wait --timeout 5m
fi

echo "==> 7. Verify"
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
