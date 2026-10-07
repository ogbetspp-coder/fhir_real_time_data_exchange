#!/usr/bin/env bash
# The certified Word recompute inside a built worker image (docs/design/certified-word-import.md,
# D2), with no network: scripts/ci/worker-recompute-smoke.mjs and the committed synthetic labels
# are copied into a container of the image (docker cp, so it works where a bind mount names the
# wrong host, as in Cloud Build) and run there. Fails on any difference from what was committed,
# or if the Word drawing's pins the gate verifies a record against do not parse in the image.
# CI's Images job (scripts/ci/build-images.sh) and Cloud Build (cloudbuild.images.yaml, before the
# image is pushed) both run it.
#
#   bash scripts/ci/worker-recompute-smoke.sh <worker image> [<labels folder>]
#
# The folder defaults to test/fixtures/certified-word/recompute; build-images.sh passes a changed
# copy once to prove a difference fails.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
image="$1"
fixtures="${2:-$ROOT/test/fixtures/certified-word/recompute}"
container=""
stage="$(mktemp -d)"
cleanup() {
  rm -rf "$stage"
  if [[ -n "$container" ]]; then docker rm --force "$container" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

cp "$ROOT/scripts/ci/worker-recompute-smoke.mjs" "$stage/smoke.mjs"
cp -R "$fixtures" "$stage/fixtures"
# Readable by the image's user: docker cp gives the files to root, with these modes.
chmod -R a+rX "$stage"
container="$(docker create --network none "$image" node /tmp/smoke/smoke.mjs /tmp/smoke/fixtures)"
docker cp "$stage" "$container:/tmp/smoke"
docker start --attach "$container"
