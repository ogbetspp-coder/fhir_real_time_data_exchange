#!/usr/bin/env bash
# What the deploy's bootstrap imports and seeds, made where no cloud credential exists (audit B08,
# D-1): the pinned standards it imports into the target store (fhir/standards.lock.json, those
# `usedBy` bootstrap) and the synthetic Type 2 fixture it seeds, which scripts/fhir/export-fixture.ts
# builds with tsx and the installed packages. Until then bootstrap.sh fetched and built both in
# the deploy job, running tsx, esbuild and zod at import time with the deployer's credentials and
# its OIDC token request in their environment.
#
# The deploy workflow runs this in its gate job, which holds no token, and hands the directory to
# the deploy job as an artifact; the SHA-256 printed last (of the directory's manifest) goes by a
# job output, and bootstrap.sh refuses a directory that does not match it or the lock
# (scripts/fhir/deploy-inputs.mjs). A local `deploy.sh all` runs it too.
#
#   bash scripts/gcp/deploy-inputs.sh <output directory>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
cd "$ROOT"

# One argument, a directory; --help prints this header, and anything else is refused.
case "$#:${1:-}" in
  1:--help | 1:-h) ema_flow_help ;;
  1:-* | 1: | 0:*) ema_flow_refuse "<output directory>" "$@" ;;
  1:*) ;;
  *) ema_flow_refuse "<output directory>" "$@" ;;
esac
OUT="$1"
if [[ -e "$OUT" && -n "$(ls -A "$OUT")" ]]; then
  echo "${OUT} is not empty; the deploy inputs are made in an empty directory." >&2
  exit 1
fi
mkdir -p "$OUT/standards"

node scripts/fhir/fetch-standards.mjs --used-by bootstrap --dest "$OUT/standards" >&2
type2="$OUT/standards/$(node scripts/fhir/fetch-standards.mjs --path "HL7 Global ePI Type 2 DrugX example")"
node_modules/.bin/tsx scripts/fhir/export-fixture.ts "$OUT/synthetic-type2.json" "$type2" >&2
sealed="$(node scripts/fhir/deploy-inputs.mjs seal "$OUT")"
# Checked here as the deploy checks them, so a fixture that no longer matches its pin
# (fhir/deploy-inputs.lock.json) fails where it was made, and in CI before a merge.
node scripts/fhir/deploy-inputs.mjs verify "$OUT" "$sealed" >&2
echo "$sealed"
