#!/usr/bin/env bash
set -euo pipefail

profile="${1:-production}"
case "$profile" in
  manual|local|full-lab|production) ;;
  *) printf 'Unsupported Compose profile: %s\n' "$profile" >&2; exit 2 ;;
esac

health_attempts="${DEPLOY_HEALTH_ATTEMPTS:-30}"
health_interval="${DEPLOY_HEALTH_INTERVAL_SECONDS:-2}"

services_for_profile() {
  case "$profile" in
    manual|local) printf '%s\n' portal-lab ;;
    full-lab) printf '%s\n' portal-lab model-api postgres minio mlflow-lab proxy-lab ;;
    production) printf '%s\n' portal-production model-api-production proxy-production ;;
  esac
}

compose() {
  docker compose --profile "$profile" "$@"
}

show_diagnostics() {
  printf 'Deployment diagnostics for profile %s:\n' "$profile" >&2
  compose ps >&2 || true
  compose logs --no-color --tail=100 >&2 || true
}

wait_for_healthy_service() {
  service="$1"
  for ((attempt = 1; attempt <= health_attempts; attempt++)); do
    container_id="$(compose ps --quiet "$service")"
    if [ -n "$container_id" ] && \
      [ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")" = "healthy" ]; then
      return 0
    fi
    sleep "$health_interval"
  done
  printf '%s did not become healthy within %s attempts.\n' "$service" "$health_attempts" >&2
  return 1
}

smoke_service() {
  service="$1"
  endpoint="$2"
  compose exec -T "$service" python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${endpoint}')"
}

verify_full_lab_champion() {
  docker compose --profile full-lab-bootstrap run --rm --no-deps model-bootstrap \
    python -m invoiceops.ml.bootstrap --verify-champion
}

verify_production_champion() {
  docker compose --profile production run --rm --no-deps model-api-production \
    python -m invoiceops.ml.bootstrap --verify-champion
}

"$(dirname "$0")/lab-preflight.sh" "$profile"
if [ "${APPLY:-0}" != "1" ]; then
  printf 'Dry run complete. Set APPLY=1 on the target VM to start the %s profile.\n' "$profile"
  exit 0
fi

if [ "$profile" = "full-lab" ] && ! verify_full_lab_champion; then
  printf 'full-lab requires a ready invoice-review@champion. Run APPLY=1 ./scripts/bootstrap-full-lab.sh first.\n' >&2
  exit 1
fi

if [ "$profile" = "production" ] && ! verify_production_champion; then
  printf 'production requires a ready invoice-review@champion. Run the private MLflow bootstrap and model release first.\n' >&2
  exit 1
fi

if [ "$profile" = "production" ]; then
  compose_up=(up --detach)
else
  compose_up=(up --detach --remove-orphans)
fi
if ! compose "${compose_up[@]}" $(services_for_profile); then
  show_diagnostics
  exit 1
fi

while IFS= read -r service; do
  if ! wait_for_healthy_service "$service"; then
    show_diagnostics
    exit 1
  fi
done < <(services_for_profile)

if [ "$profile" = "production" ]; then
  portal_service="portal-production"
else
  portal_service="portal-lab"
fi
if ! smoke_service "$portal_service" "8000/api/health"; then
  show_diagnostics
  exit 1
fi

if [ "$profile" = "full-lab" ]; then
  model_service="model-api"
elif [ "$profile" = "production" ]; then
  model_service="model-api-production"
else
  model_service=""
fi
if [ -n "$model_service" ] && ! smoke_service "$model_service" "8001/health"; then
  show_diagnostics
  exit 1
fi

printf 'Deployment healthy and smoke-tested for profile %s. No rollback was attempted.\n' "$profile"
