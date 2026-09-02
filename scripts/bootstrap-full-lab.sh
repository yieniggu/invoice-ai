#!/usr/bin/env bash
set -euo pipefail

if [ "${APPLY:-0}" != "1" ]; then
  printf 'Dry run complete. Set APPLY=1 to bootstrap invoice-review@champion for full-lab.\n'
  exit 0
fi

docker compose --profile full-lab-bootstrap config -q
docker compose --profile full-lab-bootstrap up --build --detach --wait \
  postgres minio minio-init mlflow-lab
docker compose --profile full-lab-bootstrap run --rm --no-deps model-bootstrap
printf 'Bootstrap completed. invoice-review@champion is ready; next run APPLY=1 ./scripts/deploy-lab.sh full-lab.\n'
