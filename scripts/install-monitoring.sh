#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

KUBE_CONTEXT="${KUBE_CONTEXT:?KUBE_CONTEXT must be set (e.g. k3d-pokeproxy)}"

MONITORING_NAMESPACE="monitoring"
MONITORING_NAMESPACE_MANIFEST="$REPO_ROOT/deploy/k8s/namespace-monitoring.yaml"
MONITORING_RELEASE="kube-prometheus-stack"
MONITORING_CHART_VERSION="88.5.4"
MONITORING_VALUES="$REPO_ROOT/deploy/monitoring/values.yaml"

echo "Installing monitoring stack ($MONITORING_RELEASE $MONITORING_CHART_VERSION) against kube context '$KUBE_CONTEXT'"

kubectl --context "$KUBE_CONTEXT" apply -f "$MONITORING_NAMESPACE_MANIFEST"

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update prometheus-community >/dev/null

helm upgrade --install "$MONITORING_RELEASE" prometheus-community/kube-prometheus-stack \
  --kube-context "$KUBE_CONTEXT" \
  --version "$MONITORING_CHART_VERSION" \
  --namespace "$MONITORING_NAMESPACE" \
  -f "$MONITORING_VALUES" \
  --wait --timeout 10m
