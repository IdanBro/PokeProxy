#!/usr/bin/env bash
set -euo pipefail

# Installs the CLI tools this repo needs, from official upstream sources only.
# Most tools go into ~/.local/bin (no sudo); docker is the one exception (see
# install_docker below). Two approval gates: per-tool opt-in, then one final
# summary confirmation before anything is downloaded or executed.

INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"
# Installers must see this on $PATH: several official install scripts
# (k3d, helm, tilt) self-check post-install and treat "not resolvable"
# as a hard failure, and it lets this run detect a tool a prior run
# already placed here even before the user's shell rc is reloaded.
# PATH_ALREADY_HAD_INSTALL_DIR is recorded before the export below so the
# end-of-run warning reflects the calling shell, not this process's PATH.
case ":$PATH:" in
  *":${INSTALL_DIR}:"*) PATH_ALREADY_HAD_INSTALL_DIR=1 ;;
  *) PATH_ALREADY_HAD_INSTALL_DIR=0 ;;
esac
export PATH="${INSTALL_DIR}:${PATH}"

os_name() {
  case "$(uname -s)" in
    Linux) echo "linux" ;;
    Darwin) echo "darwin" ;;
    *) echo "unsupported" ;;
  esac
}

arch_name() {
  case "$(uname -m)" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
    *) echo "unsupported" ;;
  esac
}

OS="$(os_name)"
ARCH="$(arch_name)"

if [[ "$OS" == "unsupported" || "$ARCH" == "unsupported" ]]; then
  echo "install-tools: unsupported platform ($(uname -s)/$(uname -m))." >&2
  echo "  This script supports Linux/WSL and macOS on amd64/arm64 only." >&2
  exit 1
fi

for prereq in curl tar; do
  command -v "$prereq" >/dev/null 2>&1 || {
    echo "install-tools: '${prereq}' is required but not found." >&2
    echo "  install it via your OS package manager and re-run." >&2
    exit 1
  }
done

version_at_least() {
  local have="$1" want="$2"
  [[ "$(printf '%s\n%s\n' "$have" "$want" | sort -V | head -n1)" == "$want" ]]
}

kubectl_version() { kubectl version --client=true 2>/dev/null | grep -m1 'Client Version' | sed -E 's/.*v([0-9]+\.[0-9]+\.[0-9]+).*/\1/'; }
helm_version() { helm version --short 2>/dev/null | sed -E 's/^v([0-9]+\.[0-9]+\.[0-9]+).*/\1/'; }
k3d_version() { k3d version 2>/dev/null | grep -m1 '^k3d version' | awk '{print $3}' | sed 's/^v//'; }
kubeseal_version() { kubeseal --version 2>&1 | awk '{print $3}'; }
git_version() { git --version 2>/dev/null | awk '{print $3}'; }
tilt_version() { tilt version 2>/dev/null | head -n1 | sed -E 's/^v?([0-9]+\.[0-9]+\.[0-9]+).*/\1/'; }
docker_version() { docker --version 2>/dev/null | awk '{print $3}' | tr -d ','; }

# name | floor version | version-check fn | install fn | official source (shown in the summary)
TOOLS=(kubectl helm k3d kubeseal git tilt docker)
declare -A FLOOR=( [kubectl]="1.24.0" [helm]="3.8.0" [k3d]="5.0.0" [kubeseal]="0.24.0" [git]="2.20.0" [tilt]="0.30.0" [docker]="20.10.0" )
declare -A SOURCE=(
  [kubectl]="dl.k8s.io (official Kubernetes release binaries)"
  [helm]="raw.githubusercontent.com/helm/helm (official get-helm-3 script)"
  [k3d]="raw.githubusercontent.com/k3d-io/k3d (official install.sh)"
  [kubeseal]="github.com/bitnami-labs/sealed-secrets/releases (official upstream release)"
  [git]="[apt on Linux | Homebrew/Xcode CLT on macOS] (git-scm.com's own recommended install path)"
  [tilt]="raw.githubusercontent.com/tilt-dev/tilt (official install.sh)"
  [docker]="get.docker.com (official convenience script, Linux only) | needs sudo"
)

check_version_fn() {
  case "$1" in
    kubectl) kubectl_version ;;
    helm) helm_version ;;
    k3d) k3d_version ;;
    kubeseal) kubeseal_version ;;
    git) git_version ;;
    tilt) tilt_version ;;
    docker) docker_version ;;
  esac
}

needs_install() {
  local tool="$1"
  command -v "$tool" >/dev/null 2>&1 || return 0
  local have
  have="$(check_version_fn "$tool")"
  [[ -n "$have" ]] || return 0
  ! version_at_least "$have" "${FLOOR[$tool]}"
}

# curl's own --retry only covers network-level failures, not local write
# errors (observed live: transient "curl: (23) Failure writing output to
# destination" under heavy parallel load) - so this retries the whole
# download and checks the result is actually non-empty.
curl_dl() {
  local url="$1" out="$2" attempt
  for attempt in 1 2 3; do
    curl -fsSL -o "$out" "$url" && [[ -s "$out" ]] && return 0
    sleep 2
  done
  return 1
}

curl_get() {
  local url="$1" attempt out
  for attempt in 1 2 3; do
    out="$(curl -fsSL "$url")" && [[ -n "$out" ]] && { printf '%s' "$out"; return 0; }
    sleep 2
  done
  return 1
}

install_kubectl() {
  local ver
  ver="$(curl_get "https://dl.k8s.io/release/stable.txt")"
  curl_dl "https://dl.k8s.io/release/${ver}/bin/${OS}/${ARCH}/kubectl" "${INSTALL_DIR}/kubectl"
  chmod +x "${INSTALL_DIR}/kubectl"
}

install_helm() {
  local script
  script="$(mktemp)"
  curl_dl "https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3" "$script"
  chmod +x "$script"
  HELM_INSTALL_DIR="$INSTALL_DIR" USE_SUDO=false "$script" --no-sudo
  rm -f "$script"
}

install_k3d() {
  local script
  script="$(mktemp)"
  curl_dl "https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh" "$script"
  chmod +x "$script"
  K3D_INSTALL_DIR="$INSTALL_DIR" USE_SUDO=false "$script"
  rm -f "$script"
}

install_kubeseal() {
  local ver_tag asset tmp
  ver_tag="$(curl_get https://api.github.com/repos/bitnami-labs/sealed-secrets/releases/latest | grep -m1 '"tag_name"' | sed -E 's/.*"v([^"]+)".*/\1/')"
  asset="kubeseal-${ver_tag}-${OS}-${ARCH}.tar.gz"
  tmp="$(mktemp -d)"
  curl_dl "https://github.com/bitnami-labs/sealed-secrets/releases/download/v${ver_tag}/${asset}" "${tmp}/${asset}"
  tar -xzf "${tmp}/${asset}" -C "$tmp" kubeseal
  install -m 0755 "${tmp}/kubeseal" "${INSTALL_DIR}/kubeseal"
  rm -rf "$tmp"
}

install_git() {
  if [[ "$OS" == "linux" ]]; then
    if command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y git
    else
      echo "install-tools: no apt-get found; install git via your distro's official package manager." >&2
      return 1
    fi
  else
    if command -v brew >/dev/null 2>&1; then
      brew install git
    else
      echo "install-tools: install Xcode Command Line Tools (xcode-select --install) or Homebrew, then re-run." >&2
      return 1
    fi
  fi
}

install_tilt() {
  local script
  script="$(mktemp)"
  curl_dl "https://raw.githubusercontent.com/tilt-dev/tilt/master/scripts/install.sh" "$script"
  chmod +x "$script"
  TILT_INSTALL_DIR="$INSTALL_DIR" "$script"
  rm -f "$script"
}

install_docker() {
  if [[ "$OS" == "linux" ]]; then
    local script
    script="$(mktemp)"
    curl_dl "https://get.docker.com" "$script"
    sudo sh "$script"
    rm -f "$script"
    echo "  docker installed. To run it without sudo: sudo usermod -aG docker \$USER, then log out and back in." >&2
  else
    echo "install-tools: macOS docker has no headless official installer — Docker Desktop is a GUI app." >&2
    echo "  download it yourself: https://docs.docker.com/desktop/setup/install/mac-install/" >&2
    return 1
  fi
}

run_installer() {
  case "$1" in
    kubectl) install_kubectl ;;
    helm) install_helm ;;
    k3d) install_k3d ;;
    kubeseal) install_kubeseal ;;
    git) install_git ;;
    tilt) install_tilt ;;
    docker) install_docker ;;
  esac
}

echo "==> install-tools: checking for docker? no. If you don't already have it, you don't need this script."
echo "==> install-tools: ...just kidding, I will check for that as well."
echo
echo "==> install-tools: scanning for ${TOOLS[*]}"
echo

TO_INSTALL=()
for tool in "${TOOLS[@]}"; do
  if needs_install "$tool"; then
    if command -v "$tool" >/dev/null 2>&1; then
      echo "  ${tool}: found $(check_version_fn "$tool" 2>/dev/null || echo '?'), below floor ${FLOOR[$tool]}"
    else
      echo "  ${tool}: not found"
    fi
    read -r -p "    install ${tool}? [y/N] " reply
    if [[ "$reply" =~ ^[Yy]$ ]]; then
      TO_INSTALL+=("$tool")
    fi
  else
    echo "  ${tool}: OK ($(check_version_fn "$tool"))"
  fi
done

echo
if [[ ${#TO_INSTALL[@]} -eq 0 ]]; then
  echo "==> install-tools: nothing to install."
  exit 0
fi

echo "==> install-tools: the following will be installed into ${INSTALL_DIR}:"
echo
printf '  %-10s %s\n' "TOOL" "SOURCE"
for tool in "${TO_INSTALL[@]}"; do
  printf '  %-10s %s\n' "$tool" "${SOURCE[$tool]}"
done
echo
read -r -p "Proceed with installing all of the above? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "==> install-tools: aborted, nothing installed."
  exit 0
fi

FAILED=()
for tool in "${TO_INSTALL[@]}"; do
  echo "==> installing ${tool}..."
  if run_installer "$tool"; then
    if command -v "$tool" >/dev/null 2>&1 && version_at_least "$(check_version_fn "$tool")" "${FLOOR[$tool]}"; then
      echo "    ${tool}: OK ($(check_version_fn "$tool"))"
    else
      echo "    ${tool}: installed but not resolving on PATH or still below floor" >&2
      FAILED+=("$tool")
    fi
  else
    echo "    ${tool}: install failed" >&2
    FAILED+=("$tool")
  fi
done

echo
if [[ "$PATH_ALREADY_HAD_INSTALL_DIR" -eq 0 ]]; then
  echo "==> install-tools: ${INSTALL_DIR} is not on your shell's PATH. Add it to your shell rc, e.g.:"
  echo "      export PATH=\"${INSTALL_DIR}:\$PATH\""
fi

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "==> install-tools: done with failures: ${FAILED[*]}" >&2
  exit 1
fi

echo "==> install-tools: done."
