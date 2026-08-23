#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="$REPO_ROOT/.secrets"

ENVIRONMENT=""
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
      echo "Usage: $0 --env local|prod" >&2
      exit 1
      ;;
  esac
done

case "$ENVIRONMENT" in
  local|prod) ;;
  *)
    echo "Usage: $0 --env local|prod" >&2
    exit 1
    ;;
esac

SEALING_KEY_MANIFEST="$SECRETS_DIR/sealing-key-$ENVIRONMENT.yaml"
CONTROLLER_NAMESPACE="kube-system"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_command kubectl
require_command openssl

if [[ -f "$SEALING_KEY_MANIFEST" ]]; then
  cat <<EOF >&2
$SEALING_KEY_MANIFEST already exists.

This script only provisions a key once per environment. Minting a second one
without also re-sealing and committing every value already encrypted against
the first would make the cluster undecryptable the moment Argo CD pulls that
old ciphertext from git — the exact failure this script exists to prevent
(F-2 in docs/planning/part-03-cicd-gitops.md).

If you genuinely lost the key and need to rotate it, that is a deliberate,
manual disaster-recovery act, not a re-run of this script:
  1. rm $SEALING_KEY_MANIFEST
  2. re-run this script to mint a replacement
  3. bash scripts/seal-hmac.sh --env $ENVIRONMENT to re-seal against it
  4. commit and push the re-sealed deploy/envs/$ENVIRONMENT/values.yaml yourself
Every SealedSecret ever committed against the old key becomes permanently
undecryptable the moment you do this.
EOF
  exit 1
fi

mkdir -p "$SECRETS_DIR"

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

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

cat <<EOF

Provisioned a new sealing key for '$ENVIRONMENT': $SEALING_KEY_MANIFEST

This file IS the ability to decrypt every SealedSecret sealed against it —
lose it and every committed ciphertext for this environment is permanently
unrecoverable; the fix is re-sealing from scratch, not restoring the secret.
It is gitignored (.secrets/) by design, so THIS step is your only backup
opportunity. Before running anything else:

  Back this file up now — a password manager, an encrypted vault, wherever
  you already keep credentials that must survive this laptop dying. There is
  no second chance after you close this terminal.

Once it's backed up, run:
  bash scripts/seal-hmac.sh --env $ENVIRONMENT
EOF
