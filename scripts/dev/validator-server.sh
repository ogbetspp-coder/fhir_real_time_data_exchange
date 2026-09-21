#!/usr/bin/env bash
# Runs the pinned HL7 validator locally in server mode, with the same version, packages and flags
# the deployed sidecar uses, so `scripts/dev/run-pipeline.ts` validates against what Cloud Run
# would validate against rather than something close to it.
#
# The artefacts come from the cache `npm run validate:official` populates and checksum-verifies.
# They are not re-downloaded here: a second downloader is a second thing that can drift from
# Dockerfile.validator, and this script is a convenience, not a gate.
#
# Usage: bash scripts/dev/validator-server.sh [PORT]
# Stop it with Ctrl-C. The first request after start is slow while the packages load.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CACHE="${ROOT}/.cache/official-validator"
PORT="${1:-8090}"

missing=()
for artefact in validator_cli.jar terminology-package.tgz extensions-package.tgz \
  global-epi-package.tgz ema-epi-package.tgz; do
  [[ -f "${CACHE}/${artefact}" ]] || missing+=("$artefact")
done

if ((${#missing[@]} > 0)); then
  echo "Missing from ${CACHE}: ${missing[*]}" >&2
  echo "Run 'npm run validate:official' once to download and checksum-verify them." >&2
  exit 1
fi

# The -ig order and the -tx and -version flags mirror Dockerfile.validator's CMD. Written out
# rather than held in a variable: an unquoted variable is not word-split by every shell, and a
# validator that silently runs with no packages reports zero errors and exits 0.
echo "Validator ${PORT}: loading four pinned packages, this takes about a minute."
exec java -Xms768m -Xmx1536m -jar "${CACHE}/validator_cli.jar" \
  server "${PORT}" \
  -version 5.0.0 \
  -ig "${CACHE}/terminology-package.tgz" \
  -ig "${CACHE}/extensions-package.tgz" \
  -ig "${CACHE}/global-epi-package.tgz" \
  -ig "${CACHE}/ema-epi-package.tgz" \
  -tx n/a
