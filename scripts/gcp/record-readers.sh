#!/usr/bin/env bash
# Who may read the record (docs/foundations.md, C13).
#
# Cloud Storage and BigQuery both give a project's basic roles access to every bucket and dataset
# by default: Storage through "convenience" bindings on each bucket (projectViewer reads,
# projectEditor and projectOwner write), BigQuery through special groups on each dataset
# (projectReaders read, projectWriters write, projectOwners own). So anyone ever granted Viewer
# on the project, for any reason, could read signed evidence, approved submissions, the ledger
# and the analytical copy of every label.
#
# The owner's decision, 2026-09-22: the record is read by the services that need each store,
# named in Terraform, and by the project's owners — nobody else. This script removes the viewer
# and editor paths and keeps the owner's. Keeping the owner's is deliberate: the project owner's
# own storage access comes from those bindings, and removing it locked the owner out of the
# state bucket once already (2026-09-21). Owners are the people who can grant anything anyway.
#
# Cloud Healthcare has no per-dataset equivalent to remove: Viewer and Editor on the project
# read FHIR resources in every dataset. What protects the FHIR store is that nobody holds those
# roles, so this script also reports any principal that does, by count, not by name.
#
# Run by the deploy after every apply, so a bucket or dataset created later — in production, a
# second environment — is brought into line on its first deploy. Idempotent; `--check` reports
# without changing anything.
#
#   bash scripts/gcp/record-readers.sh
#   bash scripts/gcp/record-readers.sh --check
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-sage-ship-509104-b8}"
ENVIRONMENT="${EMA_FLOW_ENVIRONMENT:-dev}"
BUCKETS=(evidence submissions profiles build-staging)
# Created by hand for the agent's deploy, outside Terraform and without the environment in its name.
EXTRA_BUCKETS=("${PROJECT_ID}-ema-flow-agent-staging")
DATASETS=("ema_flow_ledger_${ENVIRONMENT}" "ema_flow_fhir_${ENVIRONMENT}")
CHECK="false"
[[ "${1:-}" == "--check" ]] && CHECK="true"
drift=0

# Buckets: every binding held through projectViewer or projectEditor goes; projectOwner stays.
# A read that fails for any reason but "not found" stops the script: a network or permission error
# must fail the deploy, not pass it with nothing enforced.
bucket_names=()
for suffix in "${BUCKETS[@]}"; do bucket_names+=("${PROJECT_ID}-ema-flow-${ENVIRONMENT}-${suffix}"); done
bucket_names+=("${EXTRA_BUCKETS[@]}")
for bucket in "${bucket_names[@]}"; do
  err="$(mktemp)"
  if ! policy="$(gcloud --quiet storage buckets get-iam-policy "gs://${bucket}" --format=json 2>"$err")"; then
    if grep -qiE "not found|404" "$err"; then
      echo "${bucket}: does not exist; skipped"
      rm -f "$err"
      continue
    fi
    echo "${bucket}: cannot read its IAM policy: $(head -c 300 "$err")" >&2
    rm -f "$err"
    exit 1
  fi
  rm -f "$err"
  convenience="$(printf '%s' "$policy" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('bindings',[]):
    for m in b['members']:
        if m.startswith(('projectViewer:','projectEditor:')):
            print(b['role'], m)")"
  [[ -z "$convenience" ]] && continue
  drift=1
  echo "${bucket}: $(wc -l <<<"$convenience" | tr -d ' ') project viewer/editor binding(s)"
  if [[ "$CHECK" == "false" ]]; then
    while read -r role member; do
      gcloud --quiet storage buckets remove-iam-policy-binding "gs://${bucket}" \
        --role="$role" --member="$member" >/dev/null
    done <<<"$convenience"
    echo "${bucket}: removed"
  fi
done

# Datasets: the projectReaders and projectWriters special groups go; projectOwners stays. Through
# the REST API rather than the bq tool: on a fresh CI runner bq printed something other than JSON
# before its output (deploy of 2026-09-22), and the API's answer is always JSON. The PATCH carries
# If-Match with the etag read, so it applies only to the access list this script saw.
token="$(gcloud --quiet auth print-access-token)"
for dataset in "${DATASETS[@]}"; do
  url="https://bigquery.googleapis.com/bigquery/v2/projects/${PROJECT_ID}/datasets/${dataset}"
  current="$(mktemp)"
  status="$(curl --silent --show-error --output "$current" --write-out '%{http_code}' \
    --header "Authorization: Bearer ${token}" "$url")"
  if [[ "$status" == "404" ]]; then
    echo "${dataset}: does not exist; skipped"
    rm -f "$current"
    continue
  fi
  if [[ "$status" != "200" ]]; then
    echo "${dataset}: cannot read it: HTTP ${status}" >&2
    rm -f "$current"
    exit 1
  fi
  wanted="$(mktemp)"
  removed="$(python3 - "$current" "$wanted" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
keep = [a for a in d.get("access", []) if a.get("specialGroup") not in ("projectReaders", "projectWriters")]
json.dump({"access": keep}, open(sys.argv[2], "w"))
print(len(d.get("access", [])) - len(keep))
PY
)"
  etag="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['etag'])" "$current")"
  if [[ "$removed" != "0" ]]; then
    drift=1
    echo "${dataset}: ${removed} project reader/writer entr(ies)"
    if [[ "$CHECK" == "false" ]]; then
      # A 412 means the access list changed since it was read: nothing is applied, and the next
      # deploy tries again from the new list.
      status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
        --request PATCH --header "Authorization: Bearer ${token}" \
        --header "Content-Type: application/json" --header "If-Match: ${etag}" \
        --data-binary "@${wanted}" "$url")"
      if [[ "$status" != "200" ]]; then
        echo "${dataset}: update refused: HTTP ${status}" >&2
        rm -f "$current" "$wanted"
        exit 1
      fi
      echo "${dataset}: removed"
    fi
  fi
  rm -f "$current" "$wanted"
done

# Cloud Healthcare: report who holds project Viewer or Editor, by count.
basic="$(gcloud --quiet projects get-iam-policy "$PROJECT_ID" --format=json |
  python3 -c "import sys,json;print(sum(len(b['members']) for b in json.load(sys.stdin).get('bindings',[]) if b['role'] in ('roles/viewer','roles/editor')))")"
if [[ "$basic" != "0" ]]; then
  echo "::warning::${basic} principal(s) hold Viewer or Editor on ${PROJECT_ID}, which reads every FHIR store. Grant a narrower role instead."
fi

if [[ "$CHECK" == "true" ]]; then
  if [[ "$drift" == "0" ]]; then echo "No drift: the record is read by named services and the project's owners."; else echo "Drift found." >&2; exit 1; fi
fi
