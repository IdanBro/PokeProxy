#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="pokeproxy-prod"

if ! k3d cluster list "$CLUSTER_NAME" >/dev/null 2>&1; then
  echo "k3d cluster '$CLUSTER_NAME' does not exist, nothing to tear down"
  exit 0
fi

echo "==> Deleting k3d cluster '$CLUSTER_NAME'"
k3d cluster delete "$CLUSTER_NAME"

cat <<'EOF'

.secrets/sealing-key-prod.yaml is left alone -- it is the only backup of the
prod sealing key and this script never touches it. If you are done with prod
entirely, back it up and remove it yourself.
EOF
