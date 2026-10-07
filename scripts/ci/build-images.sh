#!/usr/bin/env bash
# The worker, query, signer and validator images, built from the files Cloud Build builds them from and
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
#   - the worker's image asserts its Python's Unicode version, and its recompute makes each committed
#     synthetic Word label's result again, byte for byte (the certified Word gate, D2);
#   - the validator runs as a user without root, holds no curl or wget, and starts with no
#     network at all, loading only installed packages, under universal jurisdiction in the pinned
#     locale (the same judgement, by the same script, as cloudbuild.images.yaml's
#     validator-offline-verdict step).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# The legacy builder, as Cloud Build's held docker:20.10.24 builder uses (cloudbuild.images.yaml;
# audit B07, review round 1, M-1): a Dockerfile that only BuildKit could build fails here, before
# the deploy, rather than there. Docker 28 on the runner still has it, with a deprecation notice.
export DOCKER_BUILDKIT=0

docker build --target worker --tag ema-flow/worker:ci .
docker build --target query --tag ema-flow/query:ci .
docker build --target signer --tag ema-flow/signer:ci .
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
expect_config ema-flow/signer:ci node '["node","dist/signer/server.js"]'
expect_config ema-flow/validator:ci validator '["server","8090","-allowNetworkAccess","-version","5.0.0","-jurisdiction","uv","-locale","en-US","-ig","/opt/fhir/terminology-package.tgz","-ig","/opt/fhir/extensions-package.tgz","-ig","/opt/fhir/global-epi-package.tgz","-ig","/opt/fhir/ema-epi-package.tgz","-ig","/opt/fhir/khs-epi-package.tgz","-tx","n/a","-no-http-access"]'

# The standards the worker's manifest names, read inside the image as the pipeline reads them.
docker run --rm --network none ema-flow/worker:ci node --input-type=module -e '
  const { pinnedPackages } = await import("/app/dist/fhir/standards-lock.js");
  const packages = pinnedPackages();
  if (packages.length === 0) process.exit(1);
  console.log(`worker image: ${packages.length} pinned packages in its two locks`);
'

# The certified Word recompute in the worker's image (docs/design/certified-word-import.md, D2), as
# Cloud Build runs it before pushing: the gate's own runner makes again, byte for byte, what
# `python -m zone_a.recompute` wrote for each committed synthetic label. Once more on a copy with
# one byte of one result changed, which must fail, so a smoke that cannot fail is caught here.
bash scripts/ci/worker-recompute-smoke.sh ema-flow/worker:ci
changed="$(mktemp -d)"
cp -R test/fixtures/certified-word/recompute/. "$changed"
sed -i 's/"changes":0/"changes":1/' "$changed/smpc.json"
if cmp -s "$changed/smpc.json" test/fixtures/certified-word/recompute/smpc.json; then
  echo "The smoke's changed copy is not changed." >&2
  exit 1
fi
if bash scripts/ci/worker-recompute-smoke.sh ema-flow/worker:ci "$changed" 2>/dev/null; then
  echo "The worker image's recompute smoke passed a changed result." >&2
  exit 1
fi
rm -rf "$changed"
echo "worker image: the recompute smoke fails on a changed result"

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
# The verdict by the script Cloud Build's validator-offline-verdict step runs
# (scripts/ci/validator-offline.mjs, offlineStartVerdict in validator-pins.mjs), so CI and the
# image build judge the offline start the same way.
node scripts/ci/validator-offline.mjs "$log"
rm -f "$log"
