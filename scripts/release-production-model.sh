#!/usr/bin/env bash
set -euo pipefail

run_id="${1:-}"
if [ -z "$run_id" ]; then
  printf 'Usage: %s <approved-mlflow-run-id>\n' "$0" >&2
  exit 2
fi

"$(dirname "$0")/lab-preflight.sh" production-bootstrap
docker compose --profile production-bootstrap run --rm --no-deps -e "RUN_ID=$run_id" \
  model-release-production sh -ec '
    python -m invoiceops.ml.gate --run-id "$RUN_ID"
    version="$(python -c '\''from invoiceops.ml.registry import ensure_registered_version; import os; print(ensure_registered_version(os.environ["RUN_ID"])[0])'\'')"
    VERSION="$version" python -c '\''from invoiceops.ml.registry import ensure_champion; import os; print(ensure_champion(os.environ["VERSION"], os.environ["RUN_ID"]))'\''
    printf "MODEL_VERSION=%s\\n" "$version"
  '
