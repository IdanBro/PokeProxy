#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CLUSTER_NAME="pokeproxy-prod"
KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-$CLUSTER_NAME}"
CLUSTER_CONFIG="$REPO_ROOT/deploy/k3d/cluster-prod.yaml"
NAMESPACE_MANIFEST="$REPO_ROOT/deploy/k8s/namespace.yaml"
SEAL_SCRIPT="$REPO_ROOT/scripts/seal-hmac.sh"

ARGOCD_NAMESPACE="argocd"
ARGOCD_CHART_VERSION="10.4.0"
ARGOCD_VALUES="$REPO_ROOT/deploy/argocd/install-values.yaml"
ARGOCD_APPLICATION="$REPO_ROOT/deploy/argocd/application.yaml"
ARGOCD_TARGET_REVISION="${ARGOCD_TARGET_REVISION:-main}"

APP_NAMESPACE="pokeproxy"
INGRESS_URL="http://localhost:8081/stream"

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
echo "Bootstrapping kube context '$KUBE_CONTEXT'"

echo "==> 2. Namespace"
kubectl --context "$KUBE_CONTEXT" apply -f "$NAMESPACE_MANIFEST"

echo "==> 3. Sealing key, sealed-secrets controller, sealed HMAC value"
KUBE_CONTEXT="$KUBE_CONTEXT" bash "$SEAL_SCRIPT" --env prod

echo "==> 4. Argo CD $ARGOCD_CHART_VERSION"
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update argo >/dev/null
helm upgrade --install argocd argo/argo-cd \
  --kube-context "$KUBE_CONTEXT" \
  --version "$ARGOCD_CHART_VERSION" \
  --namespace "$ARGOCD_NAMESPACE" --create-namespace \
  -f "$ARGOCD_VALUES" \
  --wait --timeout 5m

echo "==> 5. Application (targetRevision $ARGOCD_TARGET_REVISION)"
sed "s|targetRevision: main|targetRevision: $ARGOCD_TARGET_REVISION|" "$ARGOCD_APPLICATION" \
  | kubectl --context "$KUBE_CONTEXT" apply -f -

echo "==> 6. Waiting for Argo CD to converge the application"
for _ in $(seq 1 60); do
  sync_status="$(kubectl --context "$KUBE_CONTEXT" get application pokeproxy -n "$ARGOCD_NAMESPACE" \
    -o jsonpath='{.status.sync.status}' 2>/dev/null || true)"
  health_status="$(kubectl --context "$KUBE_CONTEXT" get application pokeproxy -n "$ARGOCD_NAMESPACE" \
    -o jsonpath='{.status.health.status}' 2>/dev/null || true)"
  echo "  sync=${sync_status:-unknown} health=${health_status:-unknown}"
  if [[ "$sync_status" == "Synced" && "$health_status" == "Healthy" ]]; then
    break
  fi
  sleep 10
done

if [[ "${sync_status:-}" != "Synced" || "${health_status:-}" != "Healthy" ]]; then
  echo "Argo CD did not reach Synced/Healthy. Current state:" >&2
  kubectl --context "$KUBE_CONTEXT" get application pokeproxy -n "$ARGOCD_NAMESPACE" -o wide >&2
  kubectl --context "$KUBE_CONTEXT" get pods -n "$APP_NAMESPACE" >&2
  exit 1
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

admin_password="$(kubectl --context "$KUBE_CONTEXT" get secret argocd-initial-admin-secret \
  -n "$ARGOCD_NAMESPACE" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d || true)"

cat <<EOF

Done. PokeProxy is reconciled by Argo CD and reachable at $INGRESS_URL

Argo CD UI:
  kubectl --context $KUBE_CONTEXT port-forward svc/argocd-server -n $ARGOCD_NAMESPACE 8090:80
  http://localhost:8090  (user: admin, password: ${admin_password:-<argocd-initial-admin-secret already deleted>})
EOF
