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
# Which buckets and datasets: every one infra/ declares, as the Terraform output
# record_readers_targets lists them (test/infra/record-readers.test.ts keeps that output equal to
# infra/'s resources); the Terraform state bucket, which deploy.sh creates before Terraform exists
# and which holds the entitlement map and every resource's configuration (audit I-10: until then
# it stayed readable by project viewers until storage-keys.sh was run by hand); and the agent's
# staging bucket, made by hand outside Terraform. Each one Terraform declares must exist, and so
# must the state bucket: the apply that runs before this created them and wrote to it, so a
# missing one means this is looking at another project or a stale state, and that fails rather
# than passing with nothing enforced (audit B08, D-5). Only the hand-made bucket may be absent.
#
#   bash scripts/gcp/record-readers.sh
#   bash scripts/gcp/record-readers.sh --check
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
ema_flow_option --check "$@"
cd "$ROOT"
# GOOGLE_CLOUD_PROJECT or GCP_PROJECT_ID (refused when the two differ), else the gcloud
# configuration; no project at all fails rather than falling back to a hard-coded one.
PROJECT_ID="$(ema_flow_resolve_project)"
# Created by deploy.sh before Terraform exists, so not in Terraform's list; it must exist.
STATE_BUCKETS=("$(ema_flow_state_bucket "$PROJECT_ID")")
# Created by hand for the agent's deploy, outside Terraform and without the environment in its name.
EXTRA_BUCKETS=("${PROJECT_ID}-ema-flow-agent-staging")
CHECK="false"
[[ "$EMA_FLOW_OPTION" == "--check" ]] && CHECK="true"
drift=0

if ! targets="$(terraform -chdir=infra output -json record_readers_targets 2>/dev/null)"; then
  echo "Terraform names no record_readers_targets: run after an apply of this configuration, so the state lists the buckets and datasets it declares." >&2
  exit 1
fi
declared_buckets=()
while read -r name; do if [[ -n "$name" ]]; then declared_buckets+=("$name"); fi; done < <(
  printf '%s' "$targets" | python3 -c "import sys,json;print('\n'.join(json.load(sys.stdin)['buckets']))"
)
DATASETS=()
while read -r name; do if [[ -n "$name" ]]; then DATASETS+=("$name"); fi; done < <(
  printf '%s' "$targets" | python3 -c "import sys,json;print('\n'.join(json.load(sys.stdin)['datasets']))"
)
if [[ "${#declared_buckets[@]}" == "0" || "${#DATASETS[@]}" == "0" ]]; then
  echo "record_readers_targets lists no bucket or no dataset; refusing to report that nothing needs changing." >&2
  exit 1
fi
# Every name Terraform gives is in the project this resolved: a state read from another project
# names its buckets and datasets, and they are not narrowed here.
for name in "${declared_buckets[@]}"; do
  if [[ "$name" != "${PROJECT_ID}-"* ]]; then
    echo "Terraform's bucket ${name} is not one of ${PROJECT_ID}'s: the state and the project disagree." >&2
    exit 1
  fi
done

# Buckets: every binding held through projectViewer or projectEditor goes; projectOwner stays.
# A read that fails for any reason but "not found" stops the script: a network or permission error
# must fail the deploy, not pass it with nothing enforced. "Not found" passes only for the
# hand-made bucket.
for bucket in "${declared_buckets[@]}" "${STATE_BUCKETS[@]}" "${EXTRA_BUCKETS[@]}"; do
  err="$(mktemp)"
  if ! policy="$(gcloud --quiet storage buckets get-iam-policy "gs://${bucket}" --format=json 2>"$err")"; then
    if grep -qiE "not found|404" "$err"; then
      rm -f "$err"
      if [[ " ${EXTRA_BUCKETS[*]} " == *" ${bucket} "* ]]; then
        echo "${bucket}: does not exist; skipped (made by hand, outside Terraform)"
        continue
      fi
      if [[ " ${STATE_BUCKETS[*]} " == *" ${bucket} "* ]]; then
        echo "${bucket}: the Terraform state bucket does not exist in ${PROJECT_ID}." >&2
      else
        echo "${bucket}: declared in infra/ but does not exist in ${PROJECT_ID}." >&2
      fi
      exit 1
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
token="$(ema_flow_access_token)"
for dataset in "${DATASETS[@]}"; do
  url="https://bigquery.googleapis.com/bigquery/v2/projects/${PROJECT_ID}/datasets/${dataset}"
  current="$(mktemp)"
  status="$(curl --silent --show-error --output "$current" --write-out '%{http_code}' \
    --header @<(ema_flow_header Authorization "Bearer ${token}") "$url")"
  if [[ "$status" == "404" ]]; then
    echo "${dataset}: declared in infra/ but does not exist in ${PROJECT_ID}." >&2
    rm -f "$current"
    exit 1
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
        --request PATCH --header @<(ema_flow_header Authorization "Bearer ${token}") \
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
