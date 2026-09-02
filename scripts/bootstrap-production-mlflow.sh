#!/usr/bin/env bash
set -euo pipefail

if [ "${APPLY:-0}" != "1" ]; then
  printf 'Dry run complete. Set APPLY=1 to start only private MLflow infrastructure on this VM.\n'
  exit 0
fi

"$(dirname "$0")/lab-preflight.sh" production-bootstrap
docker compose --profile production-bootstrap up --detach --wait \
  postgres-production minio-production minio-init-production mlflow-production
printf 'Private MLflow infrastructure is ready. Next run APPLY=1 ./scripts/bootstrap-production-model.sh to create and promote invoice-review@champion.\n'
