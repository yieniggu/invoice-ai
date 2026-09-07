#!/usr/bin/env bash
set -euo pipefail

readonly PORTAL_DB_PATH='/app/var/invoiceops.db'
readonly PORTAL_DATA_VOLUME='/srv/invoiceops/var'
readonly PORTAL_UID='100'
readonly PORTAL_GID='101'

fail_production_data_contract() {
  printf 'Invalid production data mount: %s\n' "$1" >&2
  exit 1
}

service_user_access() {
  local path="$1"
  local required_bits="$2"
  local metadata owner group mode permissions

  metadata="$(stat -c '%u:%g:%a' "$path")" || return 1
  IFS=: read -r owner group mode <<<"$metadata"
  [[ "$owner" =~ ^[0-9]+$ && "$group" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]+$ ]] || return 1
  permissions=$((8#$mode))
  if [ "$owner" = "$PORTAL_UID" ]; then
    permissions=$(((permissions >> 6) & 7))
  elif [ "$group" = "$PORTAL_GID" ]; then
    permissions=$(((permissions >> 3) & 7))
  else
    permissions=$((permissions & 7))
  fi
  [ $((permissions & required_bits)) -eq "$required_bits" ]
}

directory_exists() {
  [ -d "$1" ]
}

validate_production_data_mount() {
  local metadata

  [ "$INVOICEOPS_DB_PATH" = "$PORTAL_DB_PATH" ] || \
    fail_production_data_contract "INVOICEOPS_DB_PATH must be $PORTAL_DB_PATH"
  [ "$INVOICEOPS_DATA_VOLUME" = "$PORTAL_DATA_VOLUME" ] || \
    fail_production_data_contract "INVOICEOPS_DATA_VOLUME must be $PORTAL_DATA_VOLUME"
  directory_exists "$PORTAL_DATA_VOLUME" || \
    fail_production_data_contract "$PORTAL_DATA_VOLUME must exist as a directory"

  metadata="$(stat -c '%u:%g:%a' "$PORTAL_DATA_VOLUME")" || \
    fail_production_data_contract "cannot read metadata for $PORTAL_DATA_VOLUME"
  [ "$metadata" = "$PORTAL_UID:$PORTAL_GID:770" ] || \
    fail_production_data_contract "$PORTAL_DATA_VOLUME must be owned by $PORTAL_UID:$PORTAL_GID with mode 0770"

  for path in / /srv /srv/invoiceops; do
    service_user_access "$path" 1 || \
      fail_production_data_contract "service user $PORTAL_UID:$PORTAL_GID cannot traverse $path"
  done
  service_user_access "$PORTAL_DATA_VOLUME" 7 || \
    fail_production_data_contract "service user $PORTAL_UID:$PORTAL_GID cannot read, write, and traverse $PORTAL_DATA_VOLUME"
}

main() {
  local profile="${1:-manual}"
  case "$profile" in
    manual|local|full-lab|production|production-bootstrap) ;;
    *) printf 'Unsupported Compose profile: %s\n' "$profile" >&2; exit 2 ;;
  esac

  command -v docker >/dev/null || { printf 'docker is required\n' >&2; exit 1; }
  docker compose version >/dev/null

  if [ "$profile" = "production" ]; then
    for name in INVOICEOPS_IMAGE INVOICEOPS_DB_PATH INVOICEOPS_DATA_VOLUME \
      INVOICEOPS_DEMO_USERNAME INVOICEOPS_DEMO_PASSWORD INVOICEOPS_SESSION_SECRET \
      INVOICEOPS_ALLOWED_DECISION_PRINCIPALS; do
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

  if [ "$profile" = "production" ]; then
    validate_production_data_mount
  fi

  docker compose --profile "$profile" config -q
  printf 'Preflight passed for profile %s. No services or cloud resources were started.\n' "$profile"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
