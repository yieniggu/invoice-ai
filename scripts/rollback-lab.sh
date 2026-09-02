#!/usr/bin/env bash
set -euo pipefail

profile="${1:-production}"
case "$profile" in
  manual|local|full-lab|production) ;;
  *) printf 'Unsupported Compose profile: %s\n' "$profile" >&2; exit 2 ;;
esac

if [ "${APPLY:-0}" != "1" ]; then
  printf 'Dry run complete. Set APPLY=1 with a previously validated immutable INVOICEOPS_IMAGE to roll back.\n'
  exit 0
fi

INVOICEOPS_IMAGE="$("$(dirname "$0")/validate-image-reference.sh" "${INVOICEOPS_IMAGE:-}")"
export INVOICEOPS_IMAGE
"$(dirname "$0")/lab-preflight.sh" "$profile"
docker compose --profile "$profile" up --detach --remove-orphans
printf 'Rollback image requested for profile %s. Verify health and evidence before declaring recovery.\n' "$profile"
