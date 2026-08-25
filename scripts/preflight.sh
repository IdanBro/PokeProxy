#!/usr/bin/env bash
set -euo pipefail

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
  local) CLUSTER_NAME="pokeproxy" HOST_PORT="8080" ;;
  prod)  CLUSTER_NAME="pokeproxy-prod" HOST_PORT="8081" ;;
  *)
    echo "Unknown environment '$ENVIRONMENT'. Expected 'local' or 'prod'." >&2
    exit 1
    ;;
esac

fail() {
  echo "preflight: $1" >&2
  echo "  fix: $2" >&2
  exit 1
}

version_at_least() {
  local have="${1#v}" want="$2"
  [[ "$(printf '%s\n%s\n' "$have" "$want" | sort -V | head -n1)" == "$want" ]]
}

require_tool() {
  local cmd="$1" install_hint="$2"
  command -v "$cmd" >/dev/null 2>&1 || fail "'$cmd' not found on PATH" "install it: $install_hint"
}

require_version() {
  local name="$1" have="$2" want="$3" install_hint="$4"
  [[ -n "$have" ]] || fail "could not determine the installed $name version" "reinstall $name ($install_hint) and re-run"
  version_at_least "$have" "$want" || \
    fail "$name $have is older than the required $want+" "upgrade $name ($install_hint)"
}

docker_client_version() { docker --version 2>/dev/null | awk '{print $3}' | tr -d ','; }
kubectl_client_version() { kubectl version --client=true 2>/dev/null | grep -m1 'Client Version' | sed -E 's/.*v([0-9]+\.[0-9]+\.[0-9]+).*/\1/'; }
helm_client_version() { helm version --short 2>/dev/null | sed -E 's/^v([0-9]+\.[0-9]+\.[0-9]+).*/\1/'; }
k3d_client_version() { k3d version 2>/dev/null | grep -m1 '^k3d version' | awk '{print $3}' | sed 's/^v//'; }
kubeseal_client_version() { kubeseal --version 2>&1 | awk '{print $3}'; }
git_client_version() { git --version 2>/dev/null | awk '{print $3}'; }
tilt_client_version() { tilt version 2>/dev/null | head -n1 | sed -E 's/^v?([0-9]+\.[0-9]+\.[0-9]+).*/\1/'; }

echo "==> preflight: tools for env '$ENVIRONMENT'"

require_tool docker "https://docs.docker.com/get-docker/"
require_tool kubectl "https://kubernetes.io/docs/tasks/tools/install-kubectl/"
require_tool helm "https://helm.sh/docs/intro/install/"
require_tool k3d "https://k3d.io/#installation"
require_tool kubeseal "https://github.com/bitnami-labs/sealed-secrets#installation"
require_tool git "https://git-scm.com/downloads"
if [[ "$ENVIRONMENT" == "local" ]]; then
  require_tool tilt "https://docs.tilt.dev/install.html"
fi

require_version docker "$(docker_client_version)" "20.10.0" "https://docs.docker.com/get-docker/"
require_version kubectl "$(kubectl_client_version)" "1.24.0" "https://kubernetes.io/docs/tasks/tools/install-kubectl/"
require_version helm "$(helm_client_version)" "3.8.0" "https://helm.sh/docs/intro/install/"
require_version k3d "$(k3d_client_version)" "5.0.0" "https://k3d.io/#installation"
require_version kubeseal "$(kubeseal_client_version)" "0.24.0" "https://github.com/bitnami-labs/sealed-secrets#installation"
require_version git "$(git_client_version)" "2.20.0" "https://git-scm.com/downloads"
if [[ "$ENVIRONMENT" == "local" ]]; then
  require_version tilt "$(tilt_client_version)" "0.30.0" "https://docs.tilt.dev/install.html"
fi

echo "==> preflight: Docker daemon"
docker info >/dev/null 2>&1 || \
  fail "Docker daemon isn't responding" "start Docker Desktop (or the docker service) and re-run"

check_host_port_free() {
  local port="$1" expected_owner="$2" purpose="$3"
  echo "==> preflight: host port $port free for $purpose"
  local port_owner
  port_owner="$(docker ps --filter "publish=${port}/tcp" --format '{{.Names}}' 2>/dev/null | head -n1)"
  if [[ -n "$port_owner" ]]; then
    if [[ -n "$expected_owner" && "$port_owner" != "$expected_owner" ]]; then
      fail "host port $port is already published by docker container '$port_owner'" \
        "stop/remove '$port_owner', or set a different port, then re-run"
    elif [[ -z "$expected_owner" ]]; then
      fail "host port $port is already published by docker container '$port_owner'" \
        "stop/remove '$port_owner', or set a different port, then re-run"
    fi
  elif command -v ss >/dev/null 2>&1; then
    if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[.:]${port}\$"; then
      fail "host port $port is already in use by another process (not a k3d container)" \
        "find it with 'ss -ltnp | grep $port' or 'lsof -i :$port', stop it, and re-run"
    fi
  elif (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
    exec 3>&- 3<&-
    fail "host port $port is already in use by another process (not a k3d container)" \
      "find it with 'lsof -i :$port', stop it, and re-run"
  fi
}

check_host_port_free "$HOST_PORT" "k3d-${CLUSTER_NAME}-serverlb" "the '$CLUSTER_NAME' load balancer"
if [[ "$ENVIRONMENT" == "local" ]]; then
  check_host_port_free "5000" "pokeproxy-registry" "the k3d image registry"
  check_host_port_free "10350" "" "Tilt's own UI"
fi

echo "==> preflight: OK"
