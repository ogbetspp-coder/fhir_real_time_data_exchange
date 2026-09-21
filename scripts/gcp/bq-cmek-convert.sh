#!/usr/bin/env bash
# Converts the ledger and analytics tables to the ledger-analytics key, in place, without losing a
# row (docs/design/cmek-rollout.md, step 3).
#
# BigQuery can move an existing table from Google-managed encryption to a customer-managed key by
# copying it onto itself with a destination key. Two things make that unsafe if done carelessly,
# and this script refuses to proceed past either:
#
#   - Rows in a table's streaming buffer may not be included in a copy for up to 90 minutes. The
#     worker writes the ledger with streaming inserts and the Healthcare API streams the analytics
#     tables, so every table must show no buffer, with deploys paused so nothing new arrives.
#   - A copy that silently drops or alters rows would be indistinguishable from success. So each
#     table's row count and an order-independent fingerprint of every row are taken before and
#     after, and any difference stops the run; a snapshot is taken first as the way back.
#
# Before running: the deploy workflow disabled (`gh workflow disable "Deploy to Google Cloud"`),
# no run in flight, and CMEK step 1 applied so the key exists and the BigQuery encryption service
# account holds its grant.
#
#   bash scripts/gcp/bq-cmek-convert.sh --dry-run   # checks and fingerprints only
#   bash scripts/gcp/bq-cmek-convert.sh             # converts
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-sage-ship-509104-b8}"
REGION="${GCP_REGION:-europe-west4}"
ENVIRONMENT="${EMA_FLOW_ENVIRONMENT:-dev}"
KEY="projects/${PROJECT_ID}/locations/${REGION}/keyRings/ema-flow-${ENVIRONMENT}-record/cryptoKeys/ledger-analytics"
DATASETS=("ema_flow_ledger_${ENVIRONMENT}" "ema_flow_fhir_${ENVIRONMENT}")
STAMP="$(date -u +%Y%m%d%H%M)"
DRY_RUN="false"
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN="true"

tables() {
  bq ls --format=json --max_results=1000 "${PROJECT_ID}:$1" |
    python3 -c "import sys,json;[print(t['tableReference']['tableId']) for t in json.load(sys.stdin) if t['type']=='TABLE' and '_presnap_' not in t['tableReference']['tableId']]"
}

field() { # <dataset.table> <python expression over d>
  bq show --format=json "${PROJECT_ID}:$1" | python3 -c "import sys,json;d=json.load(sys.stdin);print($2)"
}

# Row count and an order-independent fingerprint of every row's full content.
fingerprint() {
  bq query --use_legacy_sql=false --format=csv --quiet \
    "SELECT COUNT(*) AS n, IFNULL(BIT_XOR(FARM_FINGERPRINT(TO_JSON_STRING(t))), 0) AS fp FROM \`${PROJECT_ID}.$1\` AS t" |
    tail -1
}

echo "Key: ${KEY}"
echo "Mode: $([[ "$DRY_RUN" == "true" ]] && echo "dry run, nothing changes" || echo "convert")"

declare -a targets=()
for dataset in "${DATASETS[@]}"; do
  for table in $(tables "$dataset"); do targets+=("${dataset}.${table}"); done
done

# Refuse the whole run if any table still has a streaming buffer: converting some and not others
# leaves a half-done state for no reason.
buffered=()
for target in "${targets[@]}"; do
  if [[ "$(field "$target" "1 if d.get('streamingBuffer') else 0")" == "1" ]]; then buffered+=("$target"); fi
done
if ((${#buffered[@]} > 0)); then
  echo "Refusing: these tables still have a streaming buffer: ${buffered[*]}" >&2
  echo "Wait until they drain (up to 90 minutes after the last write) with deploys paused." >&2
  exit 1
fi
echo "No table has a streaming buffer (${#targets[@]} tables)."

for target in "${targets[@]}"; do
  current_key="$(field "$target" "(d.get('encryptionConfiguration') or {}).get('kmsKeyName','')")"
  if [[ "$current_key" == "$KEY" ]]; then
    echo "${target}: already on the key; skipped"
    continue
  fi
  before="$(fingerprint "$target")"
  echo "${target}: before ${before} (rows,fingerprint)"
  [[ "$DRY_RUN" == "true" ]] && continue

  snapshot="${target}_presnap_${STAMP}"
  bq cp --snapshot --no_clobber --expiration 1209600 "${PROJECT_ID}:${target}" "${PROJECT_ID}:${snapshot}" >/dev/null
  echo "${target}: snapshot ${snapshot} (expires in 14 days)"

  bq cp --force --destination_kms_key "$KEY" "${PROJECT_ID}:${target}" "${PROJECT_ID}:${target}" >/dev/null

  after="$(fingerprint "$target")"
  now_key="$(field "$target" "(d.get('encryptionConfiguration') or {}).get('kmsKeyName','')")"
  if [[ "$after" != "$before" || "$now_key" != "$KEY" ]]; then
    echo "${target}: MISMATCH after conversion — before ${before}, after ${after}, key '${now_key}'." >&2
    echo "Restore with: bq cp --force ${PROJECT_ID}:${snapshot} ${PROJECT_ID}:${target}" >&2
    exit 1
  fi
  echo "${target}: after ${after}, key verified"
done
echo "Done."
