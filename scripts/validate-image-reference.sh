#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s <immutable-image-reference>\n' "$0" >&2
  exit 2
fi

# workflow_dispatch may preserve a Windows line ending; normalize it before validation.
image="${1%$'\r'}"
image_pattern='^([a-z0-9][a-z0-9._-]*(:[0-9]+)?/)?[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*@sha256:[a-f0-9]{64}$'

if [[ ! "$image" =~ $image_pattern ]]; then
  printf 'Image must be a lowercase OCI reference pinned by sha256 digest.\n' >&2
  exit 2
fi

printf '%s\n' "$image"
