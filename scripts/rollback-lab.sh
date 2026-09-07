#!/usr/bin/env bash
set -euo pipefail

profile="${1:-production}"
case "$profile" in
  manual|local|full-lab|production) ;;
  *) printf 'Unsupported Compose profile: %s\n' "$profile" >&2; exit 2 ;;
esac

services_for_profile() {
  case "$profile" in
    manual|local) printf '%s\n' portal-lab ;;
    full-lab) printf '%s\n' portal-lab model-api postgres minio mlflow-lab proxy-lab ;;
    production) printf '%s\n' anvil-classroom local-anchor-bootstrap portal-production model-api-production proxy-production ;;
  esac
}

if [ "${APPLY:-0}" != "1" ]; then
  printf 'Dry run complete. Set APPLY=1 with a previously validated immutable INVOICEOPS_IMAGE to roll back.\n'
  exit 0
fi

INVOICEOPS_IMAGE="$("$(dirname "$0")/validate-image-reference.sh" "${INVOICEOPS_IMAGE:-}")"
export INVOICEOPS_IMAGE
"$(dirname "$0")/lab-preflight.sh" "$profile"
if [ "$profile" = "production" ]; then
  docker compose --profile "$profile" up --detach $(services_for_profile)
else
  docker compose --profile "$profile" up --detach --remove-orphans
fi
printf 'Rollback image requested for profile %s. Verify health and evidence before declaring recovery.\n' "$profile"
