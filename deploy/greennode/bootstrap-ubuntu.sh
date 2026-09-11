#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this bootstrap with sudo." >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "This script requires an Ubuntu host." >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID}" != "ubuntu" ]]; then
  echo "This script supports Ubuntu only (detected: ${ID})." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git gnupg

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "No NVIDIA driver was detected. Recreate the GreenNode instance from a GPU-enabled Ubuntu image." >&2
  exit 1
fi
nvidia-smi

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

# Install and register NVIDIA's Docker runtime from its stable repository.
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

DEPLOY_USER="${SUDO_USER:-}"
if [[ -n "${DEPLOY_USER}" && "${DEPLOY_USER}" != "root" ]]; then
  usermod -aG docker "${DEPLOY_USER}"
  echo "Docker installed. Log out and back in once to use Docker without sudo."
else
  echo "Docker installed."
fi
