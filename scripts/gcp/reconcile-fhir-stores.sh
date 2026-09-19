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
ANALYTICS_DATASET="$(terraform -chdir=infra output -raw fhir_analytics_dataset)"
CHANGES_TOPIC="$(terraform -chdir=infra output -raw fhir_changes_topic)"
PARTITION_DAYS="${BIGQUERY_PARTITION_EXPIRATION_DAYS:-2555}"
PARTITION_MS="$((PARTITION_DAYS * 86400000))"

TOKEN="$(ema_flow_access_token)"
PARENT="projects/${PROJECT_ID}/locations/${REGION}/datasets/${DATASET}"
COLLECTION="https://healthcare.googleapis.com/v1/${PARENT}/fhirStores"
TMP="$(mktemp -d)"
trap 'unset TOKEN; rm -rf "$TMP"' EXIT

cat >"$TMP/source.json" <<JSON
{
  "version": "R5",
  "enableUpdateCreate": true,
  "disableReferentialIntegrity": false,
  "disableResourceVersioning": false,
  "defaultSearchHandlingStrict": true,
  "complexDataTypeReferenceParsing": "ENABLED",
  "validationConfig": {
    "disableProfileValidation": true,
    "disableRequiredFieldValidation": false,
    "disableReferenceTypeValidation": false,
    "disableFhirpathValidation": false
  },
  "labels": {
    "application": "ema-flow",
    "environment": "${EMA_FLOW_ENVIRONMENT:-dev}",
    "managed_by": "rest-reconciler"
  }
}
JSON

cat >"$TMP/target.json" <<JSON
{
  "version": "R5",
  "enableUpdateCreate": true,
  "disableReferentialIntegrity": false,
  "disableResourceVersioning": false,
  "defaultSearchHandlingStrict": true,
  "complexDataTypeReferenceParsing": "ENABLED",
  "validationConfig": {
    "disableProfileValidation": true,
    "disableRequiredFieldValidation": false,
    "disableReferenceTypeValidation": false,
    "disableFhirpathValidation": false
  },
  "notificationConfigs": [{
    "pubsubTopic": "${CHANGES_TOPIC}",
    "sendFullResource": false,
    "sendPreviousResourceOnDelete": false
  }],
  "streamConfigs": [{
    "resourceTypes": [
      "Bundle",
      "List",
      "Composition",
      "Organization",
      "MedicinalProductDefinition",
      "RegulatedAuthorization",
      "PackagedProductDefinition",
      "ManufacturedItemDefinition",
      "AdministrableProductDefinition",
      "Ingredient",
      "SubstanceDefinition"
    ],
    "bigqueryDestination": {
      "datasetUri": "bq://${PROJECT_ID}.${ANALYTICS_DATASET}",
      "writeDisposition": "WRITE_APPEND",
      "schemaConfig": {
        "schemaType": "ANALYTICS_V2",
        "recursiveStructureDepth": "5",
        "lastUpdatedPartitionConfig": {
          "type": "DAY",
          "expirationMs": "${PARTITION_MS}"
        }
      }
    }
  }],
  "labels": {
    "application": "ema-flow",
    "environment": "${EMA_FLOW_ENVIRONMENT:-dev}",
    "managed_by": "rest-reconciler"
  }
}
JSON

request() {
  local method="$1"
  local url="$2"
  local body="${3:-}"
  local args=(
    --fail-with-body --silent --show-error
    --request "$method"
    --header "Authorization: Bearer ${TOKEN}"
    --header "Content-Type: application/json"
  )
  if [[ -n "$body" ]]; then
    args+=(--data-binary "@${body}")
  fi
  curl "${args[@]}" "$url"
}

reconcile() {
  local store_id="$1"
  local body="$2"
  local resource="${COLLECTION}/${store_id}"
  local current="$TMP/${store_id}-current.json"
  local status
  status="$(curl --silent --output "$current" --write-out '%{http_code}' \
    --header "Authorization: Bearer ${TOKEN}" "$resource")"

  if [[ "$status" == "404" ]]; then
    if ! request POST "${COLLECTION}?fhirStoreId=${store_id}" "$body" >"$current"; then
      cat "$current" >&2
      echo "Failed to create ${store_id}." >&2
      exit 1
    fi
    echo "Created R5 FHIR store ${store_id}."
    return
  fi
  if [[ "$status" != "200" ]]; then
    cat "$current" >&2
    echo "Failed to inspect ${store_id}: HTTP ${status}" >&2
    exit 1
  fi

  local version
  version="$(node -e "console.log(require(process.argv[1]).version)" "$current")"
  if [[ "$version" != "R5" ]]; then
    echo "${store_id} exists with immutable version ${version}; refusing to substitute R4." >&2
    exit 1
  fi

  local update_mask
  update_mask="enableUpdateCreate,defaultSearchHandlingStrict,validationConfig,labels"
  if [[ "$store_id" == "$TARGET_STORE" ]]; then
    update_mask="${update_mask},notificationConfigs,streamConfigs"
  fi
  if ! request PATCH "${resource}?updateMask=${update_mask}" "$body" >"$current"; then
    cat "$current" >&2
    echo "Failed to update ${store_id}." >&2
    exit 1
  fi
  echo "Reconciled R5 FHIR store ${store_id}."
}

reconcile "$SOURCE_STORE" "$TMP/source.json"
reconcile "$TARGET_STORE" "$TMP/target.json"
