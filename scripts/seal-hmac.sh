#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="$REPO_ROOT/deploy/helm/pokeproxy"
SECRETS_DIR="$REPO_ROOT/.secrets"
SEALING_KEY_MANIFEST="$SECRETS_DIR/sealing-key.yaml"
VALUES_LOCAL="$CHART_DIR/values-local.yaml"

CONTROLLER_NAMESPACE="kube-system"
CONTROLLER_NAME="sealed-secrets-controller"
CONTROLLER_CHART_VERSION="2.19.3"

APP_NAMESPACE="pokeproxy"
SECRET_NAME="pokeproxy-hmac"
SECRET_KEY="POKEPROXY_HMAC_KEY"
HMAC_VALUE="${POKEPROXY_HMAC_KEY:-dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg==}"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_command kubectl
require_command helm
require_command kubeseal
require_command openssl

generate_sealing_key() {
  local work_dir
  work_dir="$(mktemp -d)"
  trap 'rm -rf "$work_dir"' RETURN

  openssl req -x509 -days 3650 -nodes -newkey rsa:4096 \
    -keyout "$work_dir/tls.key" -out "$work_dir/tls.crt" \
    -subj "/CN=sealed-secret/O=sealed-secret" 2>/dev/null

  kubectl create secret tls sealed-secrets-key \
    --namespace "$CONTROLLER_NAMESPACE" \
    --cert="$work_dir/tls.crt" --key="$work_dir/tls.key" \
    --dry-run=client -o yaml \
    | kubectl label --local -f - \
      sealedsecrets.bitnami.com/sealed-secrets-key=active \
      -o yaml \
    > "$SEALING_KEY_MANIFEST"
}

mkdir -p "$SECRETS_DIR"

if [[ ! -f "$SEALING_KEY_MANIFEST" ]]; then
  echo "Generating sealed-secrets sealing key at $SEALING_KEY_MANIFEST"
  generate_sealing_key
else
  echo "Reusing existing sealing key at $SEALING_KEY_MANIFEST"
fi

echo "Pinning sealing key into $CONTROLLER_NAMESPACE"
kubectl apply -f "$SEALING_KEY_MANIFEST" >/dev/null

echo "Installing sealed-secrets controller $CONTROLLER_CHART_VERSION"
helm repo add sealed-secrets https://bitnami.github.io/sealed-secrets >/dev/null 2>&1 || true
helm repo update sealed-secrets >/dev/null
helm upgrade --install sealed-secrets sealed-secrets/sealed-secrets \
  --version "$CONTROLLER_CHART_VERSION" \
  --namespace "$CONTROLLER_NAMESPACE" \
  --set fullnameOverride="$CONTROLLER_NAME" \
  --set image.registry=ghcr.io \
  --set keyrenewperiod="0" \
  --wait --timeout 2m >/dev/null

kubectl rollout status deployment/"$CONTROLLER_NAME" -n "$CONTROLLER_NAMESPACE" --timeout=90s >/dev/null

if [[ -f "$VALUES_LOCAL" ]] && ! grep -q "encryptedValue: CHANGEME" "$VALUES_LOCAL"; then
  echo "$VALUES_LOCAL already holds a sealed HMAC value, leaving it as-is"
  exit 0
fi

echo "Sealing HMAC key for $APP_NAMESPACE/$SECRET_NAME"
value_file="$(mktemp)"
trap 'rm -f "$value_file"' EXIT
printf '%s' "$HMAC_VALUE" > "$value_file"

encrypted_value="$(kubeseal --raw \
  --controller-name="$CONTROLLER_NAME" \
  --controller-namespace="$CONTROLLER_NAMESPACE" \
  --namespace="$APP_NAMESPACE" \
  --name="$SECRET_NAME" \
  --from-file="$SECRET_KEY=$value_file")"

cat > "$VALUES_LOCAL" <<EOF
hmac:
  encryptedValue: $encrypted_value
EOF

echo "Wrote sealed HMAC value to $VALUES_LOCAL"
