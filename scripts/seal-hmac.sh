#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="$REPO_ROOT/.secrets"

ENVIRONMENT="local"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      ENVIRONMENT="${2:-}"
      shift 2
      ;;
    --env=*)
      ENVIRONMENT="${1#*=}"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--env local|prod]" >&2
      exit 1
      ;;
  esac
done

case "$ENVIRONMENT" in
  local) CLUSTER_NAME="pokeproxy" ;;
  prod) CLUSTER_NAME="pokeproxy-prod" ;;
  *)
    echo "Unknown environment '$ENVIRONMENT'. Expected 'local' or 'prod'." >&2
    exit 1
    ;;
esac

VALUES_FILE="$REPO_ROOT/deploy/envs/$ENVIRONMENT/values.yaml"
SEALING_KEY_MANIFEST="$SECRETS_DIR/sealing-key-$ENVIRONMENT.yaml"
KUBE_CONTEXT="${KUBE_CONTEXT:-k3d-$CLUSTER_NAME}"

CONTROLLER_NAMESPACE="kube-system"
CONTROLLER_NAME="sealed-secrets-controller"
CONTROLLER_CHART_VERSION="2.19.3"

APP_NAMESPACE="pokeproxy"
SECRET_NAME="pokeproxy-hmac"
SECRET_KEY="POKEPROXY_HMAC_KEY"
DEV_HMAC_VALUE="dGVzdC1zZWNyZXQtZm9yLWxvY2FsLWRldg=="

if [[ "$ENVIRONMENT" == "local" ]]; then
  HMAC_VALUE="${POKEPROXY_HMAC_KEY:-$DEV_HMAC_VALUE}"
elif [[ -n "${POKEPROXY_HMAC_KEY:-}" ]]; then
  HMAC_VALUE="$POKEPROXY_HMAC_KEY"
else
  HMAC_VALUE="$(openssl rand -base64 32)"
  cat <<EOF >&2
POKEPROXY_HMAC_KEY was not set for '$ENVIRONMENT' -- auto-generated one for this run
(demo convenience only, not a real safeguard: nothing verifies who could have read
this value, and it is printed once below and never stored anywhere else):

  POKEPROXY_HMAC_KEY=$HMAC_VALUE

Save it now if anything needs to sign real traffic against this deployment.
EOF
fi

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_command kubectl
require_command helm
require_command kubeseal

kubectl config get-contexts "$KUBE_CONTEXT" >/dev/null 2>&1 || {
  echo "Kube context '$KUBE_CONTEXT' not found. Create the cluster first, or set KUBE_CONTEXT=<name>." >&2
  exit 1
}
echo "Sealing for environment '$ENVIRONMENT' against kube context '$KUBE_CONTEXT'"

already_sealed() {
  local file="$1" current
  [[ -f "$file" ]] || return 1
  current="$(grep -m1 -E '^[[:space:]]+encryptedValue:' "$file" | sed 's/.*encryptedValue:[[:space:]]*//')"
  [[ -n "$current" && "$current" != "CHANGEME" ]]
}

write_encrypted_value() {
  local file="$1" value="$2"

  mkdir -p "$(dirname "$file")"
  if [[ -f "$file" ]] && grep -qE '^[[:space:]]+encryptedValue:' "$file"; then
    sed -i -E "s|^([[:space:]]+encryptedValue:).*|\1 $value|" "$file"
  else
    printf 'hmac:\n  encryptedValue: %s\n' "$value" >> "$file"
  fi

  grep -qF "encryptedValue: $value" "$file" || {
    echo "Failed to write the sealed value into $file — refusing to continue." >&2
    exit 1
  }
}

mkdir -p "$SECRETS_DIR"

MINTED_THIS_RUN=false

if [[ ! -f "$SEALING_KEY_MANIFEST" ]]; then
  if [[ "$ENVIRONMENT" != "local" ]]; then
    cat <<EOF >&2
No sealing key found at $SEALING_KEY_MANIFEST.

This script does not mint one for you (see docs/planning/part-03-cicd-gitops.md):
silently generating a fresh key here would re-seal $VALUES_FILE against a key
that does not match whatever ciphertext is already committed to git, and for
'$ENVIRONMENT' that ciphertext is what Argo CD is actually reconciling from —
a mismatch surfaces 600s later as CreateContainerConfigError, not here.

Provision the key deliberately instead:
  bash scripts/init-sealing-key.sh --env $ENVIRONMENT
or restore your backed-up $SEALING_KEY_MANIFEST from wherever you saved it
when it was first provisioned.
EOF
    exit 1
  fi

  echo "No sealing key found at $SEALING_KEY_MANIFEST"
  echo "Minting one for 'local': unlike prod," \
       "Helm reads deploy/envs/local/values.yaml from the working tree, not git, so a fresh key here" \
       "is safe as long as we re-seal against it in the same run."
  bash "$REPO_ROOT/scripts/init-sealing-key.sh" --env local >/dev/null
  MINTED_THIS_RUN=true
fi
echo "Using sealing key at $SEALING_KEY_MANIFEST"

echo "Pinning sealing key into $CONTROLLER_NAMESPACE"
kubectl --context "$KUBE_CONTEXT" apply -f "$SEALING_KEY_MANIFEST" >/dev/null

echo "Installing sealed-secrets controller $CONTROLLER_CHART_VERSION"
helm repo add sealed-secrets https://bitnami.github.io/sealed-secrets >/dev/null 2>&1 || true
helm repo update sealed-secrets >/dev/null
helm upgrade --install sealed-secrets sealed-secrets/sealed-secrets \
  --kube-context "$KUBE_CONTEXT" \
  --version "$CONTROLLER_CHART_VERSION" \
  --namespace "$CONTROLLER_NAMESPACE" \
  --set fullnameOverride="$CONTROLLER_NAME" \
  --set image.registry=ghcr.io \
  --set keyrenewperiod="0" \
  --wait --timeout 2m >/dev/null

kubectl --context "$KUBE_CONTEXT" rollout status \
  deployment/"$CONTROLLER_NAME" -n "$CONTROLLER_NAMESPACE" --timeout=90s >/dev/null

if [[ "$MINTED_THIS_RUN" == "true" ]]; then
  echo "Sealing key was minted this run — re-sealing $VALUES_FILE against it regardless of what's" \
       "already there (a freshly minted keypair cannot decrypt ciphertext sealed against any other key)"
elif already_sealed "$VALUES_FILE"; then
  echo "$VALUES_FILE already holds a value sealed against the existing key, leaving it as-is"
  exit 0
fi

echo "Sealing HMAC key for $APP_NAMESPACE/$SECRET_NAME"
value_file="$(mktemp)"
trap 'rm -f "$value_file"' EXIT
printf '%s' "$HMAC_VALUE" > "$value_file"

encrypted_value="$(kubeseal --raw \
  --context "$KUBE_CONTEXT" \
  --controller-name="$CONTROLLER_NAME" \
  --controller-namespace="$CONTROLLER_NAMESPACE" \
  --namespace="$APP_NAMESPACE" \
  --name="$SECRET_NAME" \
  --from-file="$SECRET_KEY=$value_file")"

write_encrypted_value "$VALUES_FILE" "$encrypted_value"

echo "Wrote sealed HMAC value to $VALUES_FILE"
