#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

ENV_FILE=".env.production"
if [[ ! -f "${ENV_FILE}" ]]; then
  cp .env.production.example "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "Created ${SCRIPT_DIR}/${ENV_FILE}. Fill in DOMAIN, ACME_EMAIL, and SERVICE_API_KEY, then rerun this command." >&2
  exit 1
fi

chmod 600 "${ENV_FILE}"

required_vars=(DOMAIN ACME_EMAIL SERVICE_API_KEY)
for variable in "${required_vars[@]}"; do
  value="$(sed -n "s/^${variable}=//p" "${ENV_FILE}" | tail -n 1)"
  if [[ -z "${value}" || "${value}" == replace_* || "${value}" == *example.com ]]; then
    echo "Set a production value for ${variable} in ${SCRIPT_DIR}/${ENV_FILE}." >&2
    exit 1
  fi
done

provider="$(sed -n 's/^STT_PROVIDER=//p' "${ENV_FILE}" | tail -n 1)"
device="$(sed -n 's/^PHOWHISPER_DEVICE=//p' "${ENV_FILE}" | tail -n 1)"
if [[ "${provider}" != "phowhisper" ]]; then
  echo "STT_PROVIDER must be phowhisper for this GreenNode deployment." >&2
  exit 1
fi
if [[ ! "${device}" =~ ^[0-9]+$ ]]; then
  echo "PHOWHISPER_DEVICE must be a non-negative NVIDIA GPU index (normally 0)." >&2
  exit 1
fi

service_key="$(sed -n 's/^SERVICE_API_KEY=//p' "${ENV_FILE}" | tail -n 1)"
if (( ${#service_key} < 32 )); then
  echo "SERVICE_API_KEY must contain at least 32 characters. Generate one with: openssl rand -hex 32" >&2
  exit 1
fi

nvidia-smi >/dev/null
docker compose --env-file "${ENV_FILE}" config --quiet
docker compose --env-file "${ENV_FILE}" build --pull stt-service
docker compose --env-file "${ENV_FILE}" run --rm --no-deps stt-service \
  python -c 'import torch; assert torch.cuda.is_available(), "CUDA is not available inside the container"; print(torch.cuda.get_device_name(0))'

docker compose --env-file "${ENV_FILE}" pull --quiet caddy || true
if ! docker compose --env-file "${ENV_FILE}" up -d --remove-orphans \
  --wait --wait-timeout 600; then
  docker compose --env-file "${ENV_FILE}" logs --tail=200 stt-service
  exit 1
fi
docker compose --env-file "${ENV_FILE}" ps

domain="$(sed -n 's/^DOMAIN=//p' "${ENV_FILE}" | tail -n 1)"
echo "Deployment started. After DNS and TLS settle, verify https://${domain}/health"
