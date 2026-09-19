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
DATASET="$(terraform -chdir=infra output -raw healthcare_dataset_id)"
SOURCE_STORE="$(terraform -chdir=infra output -raw source_fhir_store_id)"
TARGET_STORE="$(terraform -chdir=infra output -raw target_fhir_store_id)"
PROFILE_BUCKET="$(terraform -chdir=infra output -raw profile_staging_bucket)"

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

gcloud --quiet storage rsync "$TMP/import/global" "gs://${PROFILE_BUCKET}/global" --recursive
gcloud --quiet storage rsync "$TMP/import/ema" "gs://${PROFILE_BUCKET}/ema" --recursive
gcloud --quiet storage rsync "$TMP/import/terminology" "gs://${PROFILE_BUCKET}/terminology" --recursive
gcloud --quiet storage rsync "$TMP/import/extensions" "gs://${PROFILE_BUCKET}/extensions" --recursive

for prefix in terminology extensions global ema; do
  gcloud --quiet healthcare fhir-stores import gcs "$TARGET_STORE" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --dataset="$DATASET" \
    --gcs-uri="gs://${PROFILE_BUCKET}/${prefix}/*.json" \
    --content-structure=resource-pretty
done

TYPE2_EXAMPLE=(fhir/vendor/HL7_Global_ePI_Type_2_DrugX_example-*.json)
node_modules/.bin/tsx scripts/export-fixture.ts "$TMP/synthetic-type2.json" "${TYPE2_EXAMPLE[0]}"
TOKEN="$(ema_flow_access_token)"
FHIR_BASE="https://healthcare.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/datasets/${DATASET}/fhirStores/${SOURCE_STORE}/fhir"
curl --fail-with-body --silent --show-error \
  --request PUT \
  --header "Authorization: Bearer ${TOKEN}" \
  --header "Content-Type: application/fhir+json; charset=utf-8" \
  --header "X-Request-Id: bootstrap-synthetic-type2" \
  --header "X-Goog-Healthcare-Audit-AppName: ema-flow-bootstrap" \
  --data-binary "@$TMP/synthetic-type2.json" \
  "${FHIR_BASE}/Bundle/synthetic-type2-smpc" >/dev/null
unset TOKEN

echo "Profiles imported into ${TARGET_STORE}."
echo "Synthetic source seeded at Bundle/synthetic-type2-smpc in ${SOURCE_STORE}."
echo "Run:"
echo "gcloud workflows run $(terraform -chdir=infra output -raw workflow_name) --location=${REGION} --data='{\"source\":\"healthcare-api\",\"bundleId\":\"synthetic-type2-smpc\"}'"
