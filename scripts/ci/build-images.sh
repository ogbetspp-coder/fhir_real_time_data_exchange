#!/usr/bin/env bash
# The worker, query and validator images, built from the files Cloud Build builds them from and
# checked as the deploy needs them (audit B07). Until B07 nothing built them before a merge: the
# first build of a changed Dockerfile was the deploy's. CI's Images job runs this on every pull
# request; it pushes nothing, and Cloud Build still builds the images that deploy.
#
#   bash scripts/ci/build-images.sh
#
# Checks, each failing the job:
#   - the worker's build asserts Node 22.22.0, ICU 77.1 and Unicode 16.0 in the image itself;
#   - the worker and the query service run as `node` with their own command, and the worker's
#     image holds the standards lock its run manifest names;
#   - the validator runs as a user without root, holds no curl or wget, and starts with no
#     network at all, loading only installed packages, under universal jurisdiction in the pinned
#     locale (the same judgement as cloudbuild.images.yaml's validator-starts-offline step).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

docker build --target worker --tag ema-flow/worker:ci .
docker build --target query --tag ema-flow/query:ci .
docker build --file Dockerfile.validator --tag ema-flow/validator:ci .

expect_config() { # <image> <user> <cmd as JSON>
  local actual
  actual="$(docker image inspect --format '{{.Config.User}} {{json .Config.Cmd}}' "$1")"
  if [[ "$actual" != "$2 $3" ]]; then
    echo "$1 runs as '${actual}', expected '$2 $3'." >&2
    exit 1
  fi
  echo "$1: user $2, command $3"
}
expect_config ema-flow/worker:ci node '["node","dist/server.js"]'
expect_config ema-flow/query:ci node '["node","dist/query/server.js"]'
expect_config ema-flow/validator:ci validator '["server","8090","-allowNetworkAccess","-version","5.0.0","-jurisdiction","uv","-locale","en-US","-ig","/opt/fhir/terminology-package.tgz","-ig","/opt/fhir/extensions-package.tgz","-ig","/opt/fhir/global-epi-package.tgz","-ig","/opt/fhir/ema-epi-package.tgz","-tx","n/a","-no-http-access"]'

# The standards the worker's manifest names, read inside the image as the pipeline reads them.
docker run --rm --network none ema-flow/worker:ci node --input-type=module -e '
  const { pinnedPackages } = await import("/app/dist/fhir/standards-lock.js");
  const packages = pinnedPackages();
  if (packages.length === 0) process.exit(1);
  console.log(`worker image: ${packages.length} pinned packages in fhir/standards.lock.json`);
'

if docker run --rm --network none --entrypoint sh ema-flow/validator:ci -c 'command -v curl || command -v wget'; then
  echo "The validator's runtime image still holds a download tool." >&2
  exit 1
fi
if [[ "$(docker run --rm --network none --entrypoint id ema-flow/validator:ci -u)" == "0" ]]; then
  echo "The validator runs as root." >&2
  exit 1
fi

docker rm --force offline >/dev/null 2>&1 || true
docker run --detach --name offline --network none ema-flow/validator:ci >/dev/null
for _ in $(seq 1 120); do
  if docker logs offline 2>&1 | grep -q "FHIR Validator HTTP Service started"; then break; fi
  if [[ "$(docker inspect --format '{{.State.Running}}' offline)" != "true" ]]; then break; fi
  sleep 2
done
log="$(mktemp)"
docker logs offline >"$log" 2>&1 || true
docker rm --force offline >/dev/null
VALIDATOR_LOG="$log" node --input-type=module <<'JS'
import { readFileSync } from "node:fs";
import { networkUse } from "./scripts/ci/validator-pins.mjs";

const lines = readFileSync(process.env.VALIDATOR_LOG, "utf8").split(/\r?\n/);
const { installs, other } = networkUse(lines);
const failures = [];
if (installs.length > 0) failures.push("it needed a package that is not installed in the image");
if (other.length > 0) failures.push("it attempted a network request its policy did not refuse");
if (!lines.some((line) => line.includes("FHIR Validator HTTP Service started")))
  failures.push("it did not start with networking disabled");
if (!lines.some((line) => line.includes("Jurisdiction: Global (Whole world)")))
  failures.push("it is not validating under universal jurisdiction");
if (!lines.some((line) => line.includes("Locale: United States/US")))
  failures.push("it is not running in the pinned locale");
for (const line of [...installs, ...other]) console.error(line.slice(0, 200));
if (failures.length > 0) {
  for (const line of lines.slice(-40)) console.error(line.slice(0, 200));
  console.error(`The validator image failed its offline start: ${failures.join("; ")}.`);
  process.exit(1);
}
for (const line of lines.filter((l) => /Jurisdiction:|Locale:|Package Summary|HTTP Service started/.test(l)))
  console.log(line.slice(0, 200));
JS
rm -f "$log"
