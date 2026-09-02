#!/usr/bin/env bash
set -euo pipefail

profile="${1:-manual}"
case "$profile" in
  manual|local|full-lab|production|production-bootstrap) ;;
  *) printf 'Unsupported Compose profile: %s\n' "$profile" >&2; exit 2 ;;
esac

command -v docker >/dev/null || { printf 'docker is required\n' >&2; exit 1; }
docker compose version >/dev/null

if [ "$profile" = "production" ]; then
  for name in INVOICEOPS_IMAGE INVOICEOPS_DB_PATH INVOICEOPS_DATA_VOLUME INVOICEOPS_SESSION_SECRET \
    PUBLIC_HOST TLS_EMAIL; do
    [ -n "${!name:-}" ] || { printf 'Missing required production variable: %s\n' "$name" >&2; exit 1; }
  done
fi

if [ "$profile" = "production-bootstrap" ]; then
  for name in INVOICEOPS_IMAGE MLFLOW_POSTGRES_PASSWORD MLFLOW_OBJECT_ACCESS_KEY \
    MLFLOW_OBJECT_SECRET_KEY; do
    [ -n "${!name:-}" ] || { printf 'Missing required production bootstrap variable: %s\n' "$name" >&2; exit 1; }
  done
fi

if [ "$profile" = "production" ] || [ "$profile" = "production-bootstrap" ]; then
  "$(dirname "$0")/validate-image-reference.sh" "${INVOICEOPS_IMAGE:-}" >/dev/null
fi

docker compose --profile "$profile" config -q
printf 'Preflight passed for profile %s. No services or cloud resources were started.\n' "$profile"
