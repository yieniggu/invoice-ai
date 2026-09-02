#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s <artifact-registry-invoiceops:latest>\n' "$0" >&2
  exit 2
fi

image_tag="${1%$'\r'}"
image_tag_pattern='^([a-z0-9][a-z0-9-]*)-docker\.pkg\.dev/[a-z0-9][a-z0-9-]*/invoiceops/invoiceops:latest$'
if [[ ! "$image_tag" =~ $image_tag_pattern ]]; then
  printf 'Image must be the approved Artifact Registry invoiceops:latest reference.\n' >&2
  exit 2
fi

ar_host="${image_tag%%/*}"
repository="${image_tag%:latest}"
logged_in=0

cleanup() {
  if [ "$logged_in" -eq 1 ]; then
    docker logout "https://${ar_host}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

command -v curl >/dev/null || { printf 'curl is required\n' >&2; exit 1; }
command -v docker >/dev/null || { printf 'docker is required\n' >&2; exit 1; }
command -v python3 >/dev/null || { printf 'python3 is required\n' >&2; exit 1; }

logged_in=1
curl --fail --silent --show-error \
  -H 'Metadata-Flavor: Google' \
  'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])' \
  | docker login -u oauth2accesstoken --password-stdin "https://${ar_host}" >/dev/null
docker pull "$image_tag" >/dev/null

resolved_image=""
while IFS= read -r repo_digest; do
  if [ "$repo_digest" = "${repository}@${repo_digest#*@}" ]; then
    resolved_image="$("$(dirname "$0")/validate-image-reference.sh" "$repo_digest")"
    break
  fi
done < <(docker image inspect "$image_tag" --format '{{range .RepoDigests}}{{println .}}{{end}}')

if [ -z "$resolved_image" ]; then
  printf 'Docker did not report a digest for the requested repository.\n' >&2
  exit 1
fi

printf '%s\n' "$resolved_image"
