#!/usr/bin/env bash
# The deploy's FHIR bootstrap: imports the pinned conformance resources into the target store,
# keeps the store holding exactly that set, and seeds the synthetic Type 2 source where the
# environment accepts synthetic content.
#
# Its inputs are made where no cloud credential exists (audit B08, D-1): the deploy workflow's
# gate job runs scripts/gcp/deploy-inputs.sh and hands the directory over as an artifact
# (DEPLOY_INPUTS_DIR), with the SHA-256 of its manifest as a job output (DEPLOY_INPUTS_SHA256);
# `deploy.sh all` makes them the same way. This script runs no installed package, only Node
# built-ins, and refuses inputs that do not match that hash or fhir/standards.lock.json.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
cd "$ROOT"

PROJECT_ID="$(ema_flow_resolve_project)"
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"

INPUTS="${DEPLOY_INPUTS_DIR:?DEPLOY_INPUTS_DIR names the deploy inputs made by scripts/gcp/deploy-inputs.sh}"
node scripts/fhir/deploy-inputs.mjs verify "$INPUTS" \
  "${DEPLOY_INPUTS_SHA256:?DEPLOY_INPUTS_SHA256 is the SHA-256 scripts/gcp/deploy-inputs.sh printed}"
# One pinned standard's file, by its exact name (fetch-standards.mjs --path): no glob, so no older
# pin's file can be picked instead.
standard() {
  printf '%s/standards/%s' "$INPUTS" "$(node scripts/fhir/fetch-standards.mjs --path "$1")"
}

REGION="$(terraform -chdir=infra output -raw region)"
# HEALTHCARE_DATASET_OVERRIDE points this at a dataset the services are not using yet, so a new
# dataset can be built and checked before anything is switched to it (CMEK step 5b). Unset, the
# dataset is the one Terraform says the services use.
DATASET="${HEALTHCARE_DATASET_OVERRIDE:-$(terraform -chdir=infra output -raw healthcare_dataset_id)}"
SOURCE_STORE="$(terraform -chdir=infra output -raw source_fhir_store_id)"
TARGET_STORE="$(terraform -chdir=infra output -raw target_fhir_store_id)"
PROFILE_BUCKET="$(terraform -chdir=infra output -raw profile_staging_bucket)"
TARGET_FHIR="https://healthcare.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/datasets/${DATASET}/fhirStores/${TARGET_STORE}/fhir"

TMP="$(mktemp -d)"
trap 'unset TOKEN; rm -rf "$TMP"' EXIT
mkdir -p \
  "$TMP/global" "$TMP/ema" "$TMP/terminology" "$TMP/extensions" \
  "$TMP/import/global" "$TMP/import/ema" "$TMP/import/terminology" "$TMP/import/extensions"

tar -xzf "$(standard "HL7 Global ePI package")" -C "$TMP/global"
tar -xzf "$(standard "EMA EUePI package")" -C "$TMP/ema"
tar -xzf "$(standard "HL7 R5 terminology dependency")" -C "$TMP/terminology"
tar -xzf "$(standard "HL7 R5 extensions dependency")" -C "$TMP/extensions"
node scripts/fhir/select-import-resources.mjs "$TMP/global/package" "$TMP/import/global"
node scripts/fhir/select-import-resources.mjs "$TMP/ema/package" "$TMP/import/ema"
node scripts/fhir/select-import-resources.mjs "$TMP/terminology/package" "$TMP/import/terminology"
node scripts/fhir/select-import-resources.mjs "$TMP/extensions/package" "$TMP/import/extensions"
IMPORT_TYPES="$(node scripts/fhir/select-import-resources.mjs --types)"

# The set, fingerprinted: every file's path and content, plus the dataset and store it goes into.
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
# The set's resources, "Type/id" one per line, sorted and unique: what the store must hold, of the
# import's types, and nothing more. A file that is not one resource of those types with a FHIR id
# fails here, before the store is touched.
EXPECTED="$TMP/expected.txt"
python3 - "$TMP/import" "$IMPORT_TYPES" >"$EXPECTED" <<'PY'
import json, os, re, sys
root, types = sys.argv[1], set(sys.argv[2].split())
found = set()
for dirpath, _, files in os.walk(root):
    for name in files:
        resource = json.load(open(os.path.join(dirpath, name), encoding="utf-8"))
        kind, rid = resource.get("resourceType"), resource.get("id")
        if kind not in types or not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,64}", rid):
            sys.exit(f"{name}: not a resource of the import's types with a FHIR id")
        found.add(f"{kind}/{rid}")
if not found:
    sys.exit("the import set is empty")
print("\n".join(sorted(found)))
PY
LC_ALL=C sort -u -o "$EXPECTED" "$EXPECTED"

# What the target store holds of the import's types: "Type/id versionId" per line, sorted, paged
# through FHIR search. Only ids and versions are asked for (_elements=id; the store answers with
# each resource's meta). A version the store does not report is "-". Any page that cannot be read
# fails, and a next-page link is followed only within this store.
store_listing() {
  local out="$1" type url page status
  : >"$out"
  for type in $IMPORT_TYPES; do
    url="${TARGET_FHIR}/${type}?_count=1000&_elements=id"
    while [[ -n "$url" ]]; do
      page="$TMP/page.json"
      status="$(curl --silent --show-error --output "$page" --write-out '%{http_code}' \
        --header @<(ema_flow_header Authorization "Bearer ${TOKEN}") "$url" || true)"
      if [[ "$status" != "200" ]]; then
        echo "Response summary: $(summarize_response "$page")" >&2
        echo "Could not list the ${type} resources in ${TARGET_STORE}: HTTP ${status}." >&2
        return 1
      fi
      url="$(python3 - "$page" "$type" "$TARGET_FHIR/" "$out" <<'PY'
import json, re, sys
page, kind, base, out = sys.argv[1:5]
bundle = json.load(open(page, encoding="utf-8"))
if bundle.get("resourceType") != "Bundle":
    sys.exit("the search did not answer with a Bundle")
with open(out, "a", encoding="utf-8") as listing:
    for entry in bundle.get("entry") or []:
        resource = entry.get("resource") or {}
        rid = resource.get("id")
        if resource.get("resourceType") != kind or not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,64}", rid):
            sys.exit(f"the {kind} search answered with something that is not a {kind} with a FHIR id")
        version = (resource.get("meta") or {}).get("versionId")
        version = version if isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9.-]{1,64}", version) else "-"
        listing.write(f"{kind}/{rid} {version}\n")
following = [link.get("url", "") for link in bundle.get("link") or [] if link.get("relation") == "next"]
if following and not following[0].startswith(base):
    sys.exit("a next-page link leads outside the target store")
print(following[0] if following else "")
PY
)"
    done
  done
  LC_ALL=C sort -o "$out" "$out"
}

# The store's own fingerprint: its listing, ids and versions. Any edit to a resource gives it a new
# version, so an unchanged fingerprint is an untouched set.
store_fingerprint() {
  python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$1"
}

# Skip the sync and the import when nothing would change (foundations E2). The profile bucket is
# on a customer-managed key, and Cloud Storage omits checksums from listings of such objects, so
# any sync compares by fetching each of the ~5,000 objects one at a time: measured at 13 minutes
# a deploy, for a set that changes only when a vendored package does. So after a successful
# import the marker in the bucket records two fingerprints: the set's, and the store's own (every
# resource of the import's types, with its version). The next deploy skips only if both still
# match. Until audit B08 (D-7) the store was checked by its StructureDefinition count alone, so a
# deleted ValueSet, or any resource edited in the store, was never put right. FORCE_PROFILE_IMPORT
# =true imports regardless.
MARKER="gs://${PROFILE_BUCKET}/import-fingerprint/${TARGET_STORE}.sha256"
recorded="$(gcloud --quiet storage cat "$MARKER" 2>/dev/null || true)"
recorded_set="$(printf '%s\n' "$recorded" | sed -n 1p)"
recorded_store="$(printf '%s\n' "$recorded" | sed -n 2p)"
TOKEN="$(ema_flow_access_token)"
store_listing "$TMP/store.txt"
in_store="$(store_fingerprint "$TMP/store.txt")"
missing="$(cut -d' ' -f1 "$TMP/store.txt" | LC_ALL=C sort | LC_ALL=C comm -23 "$EXPECTED" - | wc -l | tr -d ' ')"
echo "profile set ${FINGERPRINT:0:16}… (recorded ${recorded_set:0:16}…): $(wc -l <"$EXPECTED" | tr -d ' ') resources, ${missing} not in ${TARGET_STORE}; store ${in_store:0:16}… (recorded ${recorded_store:0:16}…)"
if grep -q ' -$' "$TMP/store.txt"; then
  echo "::notice title=Store versions::${TARGET_STORE} did not report every resource's version, so an edit made in the store is noticed only when it adds or removes a resource."
fi

if [[ "${FORCE_PROFILE_IMPORT:-false}" != "true" && "$recorded_set" == "$FINGERPRINT" &&
  "$recorded_store" == "$in_store" && "$missing" == "0" ]]; then
  echo "Profiles unchanged in ${TARGET_STORE} since they were imported; sync and import skipped."
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

  # The import adds and overwrites; it never removes. A resource of the import's types that the
  # set no longer has (a pin moved, an exclusion added) or that was added to the store by hand is
  # deleted, so the store validates against exactly the pinned set. Only those types are touched;
  # the pipeline's own resources are of other types. More than half the set's size is refused as a
  # listing gone wrong rather than deleted, unless FORCE_PROFILE_PRUNE=true.
  TOKEN="$(ema_flow_access_token)"
  store_listing "$TMP/store.txt"
  cut -d' ' -f1 "$TMP/store.txt" | LC_ALL=C sort | LC_ALL=C comm -13 "$EXPECTED" - >"$TMP/extra.txt"
  extra="$(wc -l <"$TMP/extra.txt" | tr -d ' ')"
  limit="$(($(wc -l <"$EXPECTED") / 2))"
  if [[ "$extra" -gt "$limit" && "${FORCE_PROFILE_PRUNE:-false}" != "true" ]]; then
    echo "::error title=Profile prune refused::${TARGET_STORE} holds ${extra} resources of the import's types that the pinned set does not, more than half the set. Nothing was deleted; check the listing, then deploy with FORCE_PROFILE_PRUNE=true if they are to go." >&2
    exit 1
  fi
  while read -r reference; do
    [[ -z "$reference" ]] && continue
    status="$(curl --silent --show-error --output "$TMP/delete.json" --write-out '%{http_code}' \
      --request DELETE --header @<(ema_flow_header Authorization "Bearer ${TOKEN}") \
      "${TARGET_FHIR}/${reference}" || true)"
    if [[ "$status" != 2?? ]]; then
      echo "Response summary: $(summarize_response "$TMP/delete.json")" >&2
      echo "Could not delete ${reference} from ${TARGET_STORE}: HTTP ${status}." >&2
      exit 1
    fi
  done <"$TMP/extra.txt"
  echo "${extra} resource(s) the pinned set does not have deleted from ${TARGET_STORE}."
  if [[ "$extra" != "0" ]]; then
    store_listing "$TMP/store.txt"
  fi
  # The import reported success, so a store that still differs from the set is not failed here
  # (a search that lags an import would fail a good deploy); it is left unrecorded, and the next
  # deploy sees the difference and imports again.
  if cut -d' ' -f1 "$TMP/store.txt" | LC_ALL=C sort | LC_ALL=C comm -3 "$EXPECTED" - | grep -q .; then
    echo "::warning title=Profile set not confirmed::${TARGET_STORE} did not list exactly the pinned set after the import; not recorded, so the next deploy imports again."
  else
    printf '%s\n%s\n' "$FINGERPRINT" "$(store_fingerprint "$TMP/store.txt")" |
      gcloud --quiet storage cp - "$MARKER" >/dev/null
    echo "Profile set ${FINGERPRINT:0:16}… recorded as imported into ${TARGET_STORE}."
  fi
fi

# The synthetic Type 2 source, seeded only where the environment accepts synthetic content
# (ALLOW_SYNTHETIC_SOURCES, from its inputs file): elsewhere the worker refuses the sources that
# would read it (docs/design/authority-import-contract.md, D7), and a synthetic bundle has no place
# in the store.
if [[ "${ALLOW_SYNTHETIC_SOURCES:-false}" != "true" ]]; then
  echo "Profiles imported into ${TARGET_STORE}. No synthetic source seeded: this environment does not accept synthetic content."
  exit 0
fi
TOKEN="$(ema_flow_access_token)"
FHIR_BASE="https://healthcare.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/datasets/${DATASET}/fhirStores/${SOURCE_STORE}/fhir"
if ! curl --fail-with-body --silent --show-error \
  --request PUT \
  --header @<(ema_flow_header Authorization "Bearer ${TOKEN}") \
  --header "Content-Type: application/fhir+json; charset=utf-8" \
  --header "X-Request-Id: bootstrap-synthetic-type2" \
  --header "X-Goog-Healthcare-Audit-AppName: ema-flow-bootstrap" \
  --data-binary "@${INPUTS}/synthetic-type2.json" \
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
