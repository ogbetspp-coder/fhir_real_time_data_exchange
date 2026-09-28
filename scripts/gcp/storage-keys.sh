#!/usr/bin/env bash
# The buckets Terraform does not create, on the platform-storage key (docs/design/cmek-rollout.md,
# step 7). The Terraform state bucket is created by `deploy.sh init` before Terraform exists to
# create anything, and the agent staging bucket was created by hand for the Agent Engine deploy.
# Since audit I-10 `deploy.sh init` creates a new state bucket on the key, with public access
# prevention and the lifecycle below, and refuses to deploy from one on any other key; this script
# brings a bucket made before that into line, and checks both.
#
# For each bucket: the default key set, public access prevention enforced, then every existing
# object rewritten under the key — a bucket's default key applies only to objects written after it
# is set. For the state bucket also:
# the legacy convenience bindings removed (projectViewer read, projectEditor/projectOwner write),
# so that the state, which holds the entitlement map and every resource's configuration, is
# readable only by identities granted storage access on purpose — the deployer (project
# storage.admin) and a named administrator, STATE_BUCKET_ADMIN, granted storage.admin on this bucket
# explicitly. That explicit grant is made BEFORE any legacy binding is removed: the project owner's
# access to a bucket comes from those legacy bindings, so removing them first locks the owner out
# of the bucket's permissions — which is what the first run of this script did, on 2026-09-21.
#
# Idempotent. Run by the owner; `--check` reports drift without changing anything.
#
#   EMA_FLOW_ENVIRONMENT=dev GCP_PROJECT_ID=sage-ship-509104-b8 bash scripts/gcp/storage-keys.sh           # apply
#   EMA_FLOW_ENVIRONMENT=dev GCP_PROJECT_ID=sage-ship-509104-b8 bash scripts/gcp/storage-keys.sh --check   # report only
set -euo pipefail

# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
ema_flow_option --check "$@"
# GOOGLE_CLOUD_PROJECT or GCP_PROJECT_ID (refused when the two differ), else the gcloud
# configuration; no project at all fails rather than falling back to a hard-coded one.
PROJECT_ID="$(ema_flow_resolve_project)"
REGION="${GCP_REGION:-europe-west4}"
# No default environment, and only the project its inputs file names (audit B08, L1).
ENVIRONMENT="$(ema_flow_require_environment "$PROJECT_ID")"
KEY="$(ema_flow_platform_key "$PROJECT_ID" "$REGION" "$ENVIRONMENT")"
STATE_BUCKET="$(ema_flow_state_bucket "$PROJECT_ID")"
STATE_BUCKET_ADMIN="${STATE_BUCKET_ADMIN:-user:khs@khsadvisory.com}"
BUCKETS=("$STATE_BUCKET" "${PROJECT_ID}-ema-flow-agent-staging")
CHECK="false"
[[ "$EMA_FLOW_OPTION" == "--check" ]] && CHECK="true"
drift=0

# Rewriting the state while Terraform holds its lock could corrupt it: a deploy is running whenever
# the lock object exists. Refuse outright rather than race it.
if gcloud --quiet storage objects describe "gs://${STATE_BUCKET}/terraform/state/default.tflock" >/dev/null 2>&1; then
  echo "Refusing: gs://${STATE_BUCKET}/terraform/state/default.tflock exists, so a deploy holds the state lock." >&2
  echo "Wait until no deploy is running." >&2
  exit 1
fi

for bucket in "${BUCKETS[@]}"; do
  if ! gcloud --quiet storage buckets describe "gs://${bucket}" >/dev/null 2>&1; then
    echo "${bucket}: does not exist; skipped"
    continue
  fi
  current="$(gcloud storage buckets describe "gs://${bucket}" --format='value(default_kms_key)')"
  if [[ "$current" != "$KEY" ]]; then
    drift=1
    echo "${bucket}: default key is '${current:-Google-managed}'"
    if [[ "$CHECK" == "false" ]]; then
      gcloud --quiet storage buckets update "gs://${bucket}" --default-encryption-key="$KEY" >/dev/null
      echo "${bucket}: default key set"
    fi
  fi

  # Never public, whatever a later grant says, like every bucket Terraform creates (audit I-8).
  prevention="$(gcloud storage buckets describe "gs://${bucket}" --format='value(public_access_prevention)')"
  if [[ "$prevention" != "enforced" ]]; then
    drift=1
    echo "${bucket}: public access prevention is '${prevention:-inherited}'"
    if [[ "$CHECK" == "false" ]]; then
      gcloud --quiet storage buckets update "gs://${bucket}" --public-access-prevention >/dev/null
      echo "${bucket}: public access prevention enforced"
    fi
  fi

  # Live objects not yet under the key. `objects list` also reports noncurrent generations in a
  # versioned bucket, which a rewrite does not touch; `ls` lists live objects only.
  unkeyed=""
  while read -r url; do
    [[ -z "$url" ]] && continue
    key_of="$(gcloud storage objects describe "$url" --format='value(kms_key)' 2>/dev/null || true)"
    [[ "$key_of" == "$KEY"* ]] || unkeyed+="${url}"$'\n'
  done < <(gcloud storage ls "gs://${bucket}/**" 2>/dev/null || true)
  count="$(printf '%s' "$unkeyed" | grep -c . || true)"
  if [[ "$count" -gt 0 ]]; then
    drift=1
    echo "${bucket}: ${count} object(s) not under the key"
    if [[ "$CHECK" == "false" ]]; then
      gcloud --quiet storage objects update "gs://${bucket}/**" --encryption-key="$KEY" >/dev/null
      echo "${bucket}: objects rewritten under the key"
    fi
  fi
done

# Old generations of the state expire (common.sh, ema_flow_state_lifecycle, which says why).
lifecycle="$(mktemp)"
storage_keys_cleanup() { rm -f "$lifecycle"; }
ema_flow_on_exit storage_keys_cleanup
ema_flow_state_lifecycle >"$lifecycle"
current_rules="$(gcloud storage buckets describe "gs://${STATE_BUCKET}" --format=json |
  python3 -c "import sys,json;print(json.dumps(sorted(json.dumps(r,sort_keys=True) for r in (json.load(sys.stdin).get('lifecycle_config') or {}).get('rule',[]))))")"
wanted_rules="$(python3 -c "import sys,json;print(json.dumps(sorted(json.dumps(r,sort_keys=True) for r in json.load(open(sys.argv[1]))['rule'])))" "$lifecycle")"
if [[ "$current_rules" != "$wanted_rules" ]]; then
  drift=1
  echo "${STATE_BUCKET}: noncurrent state generations are kept forever"
  if [[ "$CHECK" == "false" ]]; then
    gcloud --quiet storage buckets update "gs://${STATE_BUCKET}" --lifecycle-file="$lifecycle" >/dev/null
    echo "${STATE_BUCKET}: noncurrent generations now expire (20 newer, or 30 days)"
  fi
fi

# The named administrator's explicit grant, before anything is removed.
has_admin="$(gcloud storage buckets get-iam-policy "gs://${STATE_BUCKET}" --format=json |
  python3 -c "import sys,json;print(any(b['role']=='roles/storage.admin' and '${STATE_BUCKET_ADMIN}' in b['members'] for b in json.load(sys.stdin).get('bindings',[])))")"
if [[ "$has_admin" != "True" ]]; then
  drift=1
  echo "${STATE_BUCKET}: ${STATE_BUCKET_ADMIN} holds no explicit storage.admin"
  if [[ "$CHECK" == "false" ]]; then
    gcloud --quiet storage buckets add-iam-policy-binding "gs://${STATE_BUCKET}" \
      --role=roles/storage.admin --member="$STATE_BUCKET_ADMIN" >/dev/null
    echo "${STATE_BUCKET}: ${STATE_BUCKET_ADMIN} granted storage.admin on the bucket"
  fi
fi

# The state bucket's legacy convenience bindings.
legacy="$(gcloud storage buckets get-iam-policy "gs://${STATE_BUCKET}" --format=json |
  python3 -c "import sys,json;print('\n'.join(f\"{b['role']} {m}\" for b in json.load(sys.stdin).get('bindings',[]) for m in b['members'] if m.startswith(('projectViewer:','projectEditor:','projectOwner:'))))")"
if [[ -n "$legacy" ]]; then
  drift=1
  echo "${STATE_BUCKET}: legacy bindings present:"
  printf '  %s\n' "$legacy"
  if [[ "$CHECK" == "false" ]]; then
    while read -r role member; do
      gcloud --quiet storage buckets remove-iam-policy-binding "gs://${STATE_BUCKET}" \
        --role="$role" --member="$member" >/dev/null
    done <<<"$legacy"
    echo "${STATE_BUCKET}: legacy bindings removed"
  fi
fi

if [[ "$CHECK" == "true" ]]; then
  [[ "$drift" == "0" ]] && echo "No drift." || { echo "Drift found." >&2; exit 1; }
else
  bash "$0" --check
fi
ema_flow_finish
