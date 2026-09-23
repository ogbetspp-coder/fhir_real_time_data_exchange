#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
cd "$ROOT"

PROJECT_ID="$(ema_flow_resolve_project)"
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"

REGION="$(terraform -chdir=infra output -raw region)"
# HEALTHCARE_DATASET_OVERRIDE points this at a dataset the services are not using yet, so a new
# dataset can be built and checked before anything is switched to it (CMEK step 5b). Unset, the
# dataset is the one Terraform says the services use.
DATASET="${HEALTHCARE_DATASET_OVERRIDE:-$(terraform -chdir=infra output -raw healthcare_dataset_id)}"
SOURCE_STORE="$(terraform -chdir=infra output -raw source_fhir_store_id)"
TARGET_STORE="$(terraform -chdir=infra output -raw target_fhir_store_id)"
PROFILE_BUCKET="$(terraform -chdir=infra output -raw profile_staging_bucket)"

summarize_response() {
  node -e '
const fs = require("node:fs");
const crypto = require("node:crypto");
const raw = fs.readFileSync(process.argv[1]);
const digest = () =>
  `unrecognised body sha256=${crypto.createHash("sha256").update(raw).digest("hex")}`;
const token = (value, pattern) => (typeof value === "string" && pattern.test(value) ? value : "?");
let body;
try {
  body = JSON.parse(raw.toString("utf8"));
} catch {
  body = undefined;
}
if (body === null || typeof body !== "object") {
  console.log(digest());
} else if (body.error !== null && typeof body.error === "object") {
  const code = Number.isInteger(body.error.code) ? String(body.error.code) : "?";
  console.log(`code=${code} status=${token(body.error.status, /^[A-Z0-9_]{1,64}$/)}`);
} else if (body.resourceType === "OperationOutcome") {
  const issues = Array.isArray(body.issue) ? body.issue : [];
  const column = (name) =>
    issues
      .map((issue) =>
        issue === null || typeof issue !== "object" ? "?" : token(issue[name], /^[a-z-]{1,64}$/),
      )
      .join(",");
  const summary = `issues=${issues.length} codes=${column("code")} severities=${column("severity")}`;
  console.log(`OperationOutcome ${summary}`);
} else {
  console.log(digest());
}
' "$1" 2>/dev/null || echo "unrecognised body sha256=unavailable"
}

npm run standards:fetch

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p \
  "$TMP/global" "$TMP/ema" "$TMP/terminology" "$TMP/extensions" \
  "$TMP/import/global" "$TMP/import/ema" "$TMP/import/terminology" "$TMP/import/extensions"

GLOBAL_PACKAGE=(fhir/vendor/HL7_Global_ePI_package-*.tgz)
EMA_PACKAGE=(fhir/vendor/EMA_EUePI_package-*.tgz)
TERMINOLOGY_PACKAGE=(fhir/vendor/HL7_R5_terminology_dependency-*.tgz)
EXTENSIONS_PACKAGE=(fhir/vendor/HL7_R5_extensions_dependency-*.tgz)
tar -xzf "${GLOBAL_PACKAGE[0]}" -C "$TMP/global"
tar -xzf "${EMA_PACKAGE[0]}" -C "$TMP/ema"
tar -xzf "${TERMINOLOGY_PACKAGE[0]}" -C "$TMP/terminology"
tar -xzf "${EXTENSIONS_PACKAGE[0]}" -C "$TMP/extensions"
node scripts/fhir/select-import-resources.mjs "$TMP/global/package" "$TMP/import/global"
node scripts/fhir/select-import-resources.mjs "$TMP/ema/package" "$TMP/import/ema"
node scripts/fhir/select-import-resources.mjs "$TMP/terminology/package" "$TMP/import/terminology"
node scripts/fhir/select-import-resources.mjs "$TMP/extensions/package" "$TMP/import/extensions"

# Skip the sync and the import when nothing would change (foundations E2). The profile bucket is
# on a customer-managed key, and Cloud Storage omits checksums from listings of such objects, so
# any sync compares by fetching each of the ~5,000 objects one at a time: measured at 13 minutes
# a deploy, for a set that changes only when a vendored package does. So the generated set is
# fingerprinted — every file's path and content, plus the dataset and store it goes into — and
# the fingerprint is recorded in the bucket after a successful import. The next deploy skips
# both steps only if the fingerprint matches AND the store itself still holds the expected
# number of StructureDefinitions (753 on 2026-09-22), so a recreated or emptied store is always
# re-imported. The Healthcare API refuses `_summary=count`; `_total=accurate` gives the count.
# FORCE_PROFILE_IMPORT=true imports regardless.
FINGERPRINT="$(python3 - "$TMP/import" "$DATASET" "$TARGET_STORE" <<'PY'
import hashlib, os, sys
root, dataset, store = sys.argv[1], sys.argv[2], sys.argv[3]
h = hashlib.sha256(f"dataset={dataset}\nstore={store}\n".encode())
for dirpath, _, files in sorted(os.walk(root)):
    for name in sorted(files):
        path = os.path.join(dirpath, name)
        h.update(os.path.relpath(path, root).encode() + b"\0")
        h.update(hashlib.sha256(open(path, "rb").read()).digest())
print(h.hexdigest())
PY
)"
MARKER="gs://${PROFILE_BUCKET}/import-fingerprint/${TARGET_STORE}.sha256"
EXPECTED_PROFILES="$(find "$TMP/import" -type f -name 'StructureDefinition-*.json' | wc -l | tr -d ' ')"
recorded="$(gcloud --quiet storage cat "$MARKER" 2>/dev/null || true)"
in_store="$(curl --fail --silent --show-error \
  --header "Authorization: Bearer $(ema_flow_access_token)" \
  "https://healthcare.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/datasets/${DATASET}/fhirStores/${TARGET_STORE}/fhir/StructureDefinition?_count=1&_total=accurate&_elements=id" |
  python3 -c "import sys,json;print(json.load(sys.stdin).get('total',''))" 2>/dev/null || true)"
echo "profile set ${FINGERPRINT:0:16}…: recorded ${recorded:0:16}…, StructureDefinitions in store ${in_store:-unknown}, expected ${EXPECTED_PROFILES}"

if [[ "${FORCE_PROFILE_IMPORT:-false}" != "true" && "$recorded" == "$FINGERPRINT" && "$in_store" == "$EXPECTED_PROFILES" ]]; then
  echo "Profiles unchanged and present in ${TARGET_STORE}; sync and import skipped."
else
  # --delete-unmatched-destination-objects: without it, rsync only adds/updates
  # objects, so a file excluded here after already having been uploaded by an
  # earlier run (e.g. select-import-resources.mjs's exclusion list) would keep
  # being imported from the stale copy left in the bucket.
  #
  # --checksums-only, and one fixed modification time on every generated file: the files are
  # written fresh on every deploy, so by default rsync saw a new mtime on each of the ~5,000 and
  # re-uploaded them all. Once the bucket moved to a customer-managed key (CMEK step 7) each
  # upload also costs a key operation, and the re-upload took the deploy's bootstrap from four
  # minutes to over twenty. Comparing content hashes uploads exactly the files whose content
  # changed; the fixed mtime stops rsync patching every object's timestamp when nothing did.
  # The comparison runs on MD5, which every object here carries, rather than on CRC32C through the
  # `gcloud-crc32c` helper binary, which some gcloud installs lack — without it rsync stops part-way.
  find "$TMP/import" -type f -exec touch -t 198001010000 {} +
  export CLOUDSDK_STORAGE_USE_GCLOUD_CRC32C=false
  gcloud --quiet storage rsync "$TMP/import/global" "gs://${PROFILE_BUCKET}/global" --recursive --checksums-only --delete-unmatched-destination-objects
  gcloud --quiet storage rsync "$TMP/import/ema" "gs://${PROFILE_BUCKET}/ema" --recursive --checksums-only --delete-unmatched-destination-objects
  gcloud --quiet storage rsync "$TMP/import/terminology" "gs://${PROFILE_BUCKET}/terminology" --recursive --checksums-only --delete-unmatched-destination-objects
  gcloud --quiet storage rsync "$TMP/import/extensions" "gs://${PROFILE_BUCKET}/extensions" --recursive --checksums-only --delete-unmatched-destination-objects

  for prefix in terminology extensions global ema; do
    echo "=== importing ${prefix} ==="
    import_log="$TMP/import-${prefix}.log"
    if ! gcloud --quiet healthcare fhir-stores import gcs "$TARGET_STORE" \
      --project="$PROJECT_ID" \
      --location="$REGION" \
      --dataset="$DATASET" \
      --gcs-uri="gs://${PROFILE_BUCKET}/${prefix}/*.json" \
      --content-structure=resource-pretty 2>&1 | tee "$import_log"; then
      # The CLI only reports a summary error pointing at metadata.logsUrl for the
      # per-resource details; describing the operation surfaces the counters and
      # that URL instead of just the bare invalid_argument. Neither the describe
      # output nor the log read may carry response text: upstream messages can
      # quote FHIR content, so only codes, counters and identifiers are printed.
      operation_id="$(grep -oE 'operations/[0-9]+' "$import_log" | head -1 | cut -d/ -f2)"
      if [[ -n "$operation_id" ]]; then
        echo "=== operation details for ${prefix} import (operation ${operation_id}) ===" >&2
        gcloud --quiet healthcare operations describe "$operation_id" \
          --project="$PROJECT_ID" --location="$REGION" --dataset="$DATASET" \
          --format="value(done,error.code,metadata.counter.failure,metadata.counter.success,metadata.logsUrl)" >&2 || true
        # The operation's own metadata only has success/failure counts, not the
        # per-resource errors -- those are in Cloud Logging under this operation id.
        echo "=== per-resource import errors for ${prefix} (operation ${operation_id}) ===" >&2
        gcloud --quiet logging read \
          "operation.id=\"projects/${PROJECT_ID}/locations/${REGION}/datasets/${DATASET}/operations/${operation_id}\"" \
          --project="$PROJECT_ID" \
          --format="value(timestamp,severity,jsonPayload.resourceId,jsonPayload.error.code)" \
          --limit=100 >&2 || true
      fi
      exit 1
    fi
  done
  printf '%s\n' "$FINGERPRINT" | gcloud --quiet storage cp - "$MARKER" >/dev/null
  echo "Profile set ${FINGERPRINT:0:16}… recorded as imported into ${TARGET_STORE}."
fi

# This artifact has no version/package in standards.lock.json, so
# fetch-standards.mjs writes it with no suffix at all: no literal "-" to
# anchor on here (unlike the other, packaged vendor files below).
TYPE2_EXAMPLE=(fhir/vendor/HL7_Global_ePI_Type_2_DrugX_example*.json)
node_modules/.bin/tsx scripts/fhir/export-fixture.ts "$TMP/synthetic-type2.json" "${TYPE2_EXAMPLE[0]}"
TOKEN="$(ema_flow_access_token)"
FHIR_BASE="https://healthcare.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/datasets/${DATASET}/fhirStores/${SOURCE_STORE}/fhir"
if ! curl --fail-with-body --silent --show-error \
  --request PUT \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Content-Type: application/fhir+json; charset=utf-8" \
  --header "X-Request-Id: bootstrap-synthetic-type2" \
  --header "X-Goog-Healthcare-Audit-AppName: ema-flow-bootstrap" \
  --data-binary "@$TMP/synthetic-type2.json" \
  "${FHIR_BASE}/Bundle/synthetic-type2-smpc" >"$TMP/bootstrap-response.json"; then
  echo "Response summary: $(summarize_response "$TMP/bootstrap-response.json")" >&2
  echo "Failed to seed the synthetic Type 2 bundle." >&2
  exit 1
fi
unset TOKEN

echo "Profiles imported into ${TARGET_STORE}."
echo "Synthetic source seeded at Bundle/synthetic-type2-smpc in ${SOURCE_STORE}."
echo "Run:"
echo "gcloud workflows run $(terraform -chdir=infra output -raw workflow_name) --location=${REGION} --data='{\"source\":\"healthcare-api\",\"bundleId\":\"synthetic-type2-smpc\"}'"
