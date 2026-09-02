#!/usr/bin/env bash
set -euo pipefail

if [ "${APPLY:-0}" != "1" ]; then
  printf 'Dry run complete. Set APPLY=1 after private MLflow infrastructure is healthy to bootstrap invoice-review@champion.\n'
  exit 0
fi

"$(dirname "$0")/lab-preflight.sh" production-bootstrap
docker compose --profile production-bootstrap run --rm --no-deps model-bootstrap-production
printf 'Model bootstrap completed. invoice-review@champion is ready; next run APPLY=1 ./scripts/deploy-lab.sh production.\n'
