#!/usr/bin/env bash
# The product's landing zone (docs/foundations.md, A1 and A2's policy).
#
#   khsadvisory.com
#   └── EMA Flow                         folder
#       ├── non-production               folder
#       │   └── sage-ship-509104-b8      the dev project, moved here
#       └── production                   folder: EU-only, CMEK-only
#           └── PROD_PROJECT_ID          created empty, no billing, nothing deployed
#
# Why the policies sit on the production folder and not on the product folder: the dev project
# hosts the Gemini Enterprise trial, whose app and data store are `global`, and a location policy
# would refuse the next global resource it needs. EU residency of the assistant is a production
# gate item (roadmap), so the policy goes where production lives. Every future production project
# (a separate key project, a second region) inherits it by being created in that folder.
#
# Production-folder policies:
#   - gcp.resourceLocations = in:eu-locations. New resources outside the EU are refused.
#   - gcp.restrictNonCmekServices denies storage, bigquery and artifactregistry: a new bucket,
#     dataset or repository without a customer-managed key is refused. The Cloud Healthcare API is
#     not a value this constraint accepts (the API refused it on the first run, 2026-09-22), so the
#     FHIR dataset's key is held by Terraform instead: the dataset declares it, carries
#     prevent_destroy, and test/infra/keys.test.ts fails without it. Logging
#     is deliberately not listed: every new project's default log buckets are created by Google
#     without a key, and the regulated audit bucket is keyed explicitly in Terraform.
#   - gcp.restrictCmekCryptoKeyProjects = under:folders/<production>. A production resource cannot
#     be encrypted with a key held in dev or anywhere outside production.
#
# Moving the project changes nothing it runs: no Terraform or script refers to its parent, the
# organisation's policies and IAM are inherited through the folders unchanged, and the project's
# own policy override, deny policy, sinks and Workload Identity pool move with it.
#
# Order, chosen so a failure part-way never leaves dev moved without production guarded: folders,
# then the production folder's policies, then the prod project, then — last — the dev move.
#
# Needs, on the organisation: roles/resourcemanager.folderAdmin (create folders, move projects;
# organizationAdmin has neither) and roles/orgpolicy.policyAdmin; project creation is already
# granted to the domain. The script checks the first before changing anything. Idempotent;
# `--check` reports drift without changing anything.
#
#   bash scripts/gcp/landing-zone.sh
#   bash scripts/gcp/landing-zone.sh --check
set -euo pipefail

ORG_ID="${GCP_ORG_ID:-1048405016186}"
# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
# GOOGLE_CLOUD_PROJECT or GCP_PROJECT_ID (refused when the two differ), else the gcloud
# configuration; no project at all fails rather than falling back to a hard-coded one.
DEV_PROJECT="$(ema_flow_resolve_project)"
PROD_PROJECT="${PROD_PROJECT_ID:-khs-ema-flow-prod}"
CHECK="false"
[[ "${1:-}" == "--check" ]] && CHECK="true"
drift=0

note() { drift=1; echo "$*"; }
die() { echo "$*" >&2; exit 1; }

parent_flag() { # organizations/1 -> --organization=1 ; folders/2 -> --folder=2
  [[ "${1%%/*}" == "organizations" ]] && echo "--organization=${1#*/}" || echo "--folder=${1#*/}"
}

folder_id() { # <display name> <parent resource>
  # stdout is the result; stderr carries gcloud's warnings, shown only if the listing fails.
  local out err
  err="$(mktemp)"
  out="$(gcloud resource-manager folders list "$(parent_flag "$2")" \
    --filter="displayName=\"$1\"" --format='value(name)' 2>"$err")" ||
    die "listing folders under $2 failed: $(cat "$err")"
  rm -f "$err"
  printf '%s\n' "$out" | head -1 | sed 's|.*/||'
}

ensure_folder() { # <result variable> <display name> <parent resource>
  local id
  id="$(folder_id "$2" "$3")"
  if [[ -z "$id" ]]; then
    note "folder '$2' under $3: missing"
    if [[ "$CHECK" == "false" ]]; then
      gcloud resource-manager folders create --display-name="$2" "$(parent_flag "$3")" --quiet >/dev/null
      for _ in $(seq 1 30); do # folder listing is eventually consistent
        id="$(folder_id "$2" "$3")"; [[ -n "$id" ]] && break; sleep 2
      done
      [[ -n "$id" ]] || die "folder '$2' created but not yet listed; re-run"
      echo "folder '$2': created (folders/$id)"
    fi
  fi
  printf -v "$1" '%s' "$id"
}

# Preflight: the permission to create folders and move projects, before anything changes.
if [[ "$CHECK" == "false" ]]; then
  token="$(gcloud auth print-access-token)"
  # The token is read from a pipe, never put in curl's arguments (common.sh, ema_flow_header).
  granted="$(curl -fsS -X POST -H @<(ema_flow_header Authorization "Bearer ${token}") \
    -H "Content-Type: application/json" \
    -d '{"permissions":["resourcemanager.folders.create","resourcemanager.projects.move","orgpolicy.policies.create"]}' \
    "https://cloudresourcemanager.googleapis.com/v3/organizations/${ORG_ID}:testIamPermissions" |
    python3 -c "import sys,json;print(len(json.load(sys.stdin).get('permissions',[])))")"
  [[ "$granted" == "3" ]] || die "Refusing: the active account lacks folder creation, project moves or policy
administration on the organisation. Grant roles/resourcemanager.folderAdmin first (see the header)."
fi

# 1. Folders.
ensure_folder product "EMA Flow" "organizations/${ORG_ID}"
[[ -z "$product" ]] && die "Drift found: the EMA Flow folder does not exist."
ensure_folder nonprod "non-production" "folders/${product}"
ensure_folder prod "production" "folders/${product}"
[[ -z "$nonprod" || -z "$prod" ]] && die "Drift found: a sub-folder does not exist."

# 2. The production folder's policies, before anything lives in it.
policy_dir="$(mktemp -d)"
trap 'rm -rf "$policy_dir"' EXIT
policy() { # <constraint> <values json>
  printf '{"name":"folders/%s/policies/%s","spec":{"rules":[{"values":%s}]}}\n' "$prod" "$1" "$2" \
    >"$policy_dir/$1.json"
}
policy gcp.resourceLocations '{"allowedValues":["in:eu-locations"]}'
policy gcp.restrictNonCmekServices '{"deniedValues":["artifactregistry.googleapis.com","bigquery.googleapis.com","storage.googleapis.com"]}'
policy gcp.restrictCmekCryptoKeyProjects "{\"allowedValues\":[\"under:folders/${prod}\"]}"

canonical() { # policy JSON on stdin -> canonical spec (rules, reset, inheritFromParent) and dry run
  python3 -c "
import sys,json
t=sys.stdin.read().strip()
if not t: print('unset'); sys.exit()
d=json.loads(t)
def norm(spec):
  spec=dict(spec or {}); spec.pop('etag',None); spec.pop('updateTime',None)
  for x in spec.get('rules',[]):
    for k in list(x.get('values',{})): x['values'][k]=sorted(x['values'][k])
  return spec
print(json.dumps({'spec':norm(d.get('spec')),'dryRunSpec':norm(d.get('dryRunSpec')) if d.get('dryRunSpec') else None},sort_keys=True))"
}

for f in "$policy_dir"/*.json; do
  constraint="$(basename "$f" .json)"
  want="$(canonical <"$f")"
  have="$(gcloud org-policies describe "$constraint" --folder="$prod" --format=json 2>/dev/null | canonical || true)"
  if [[ "$have" != "$want" ]]; then
    note "production folder: ${constraint} is ${have}"
    if [[ "$CHECK" == "false" ]]; then
      gcloud org-policies set-policy "$f" --quiet >/dev/null
      echo "production folder: ${constraint} set"
    fi
  fi
done

# 3. The empty prod project. Project ids are global: a 403 on describe means missing or someone
#    else's, and a failed create is almost always the latter.
if ! gcloud projects describe "$PROD_PROJECT" >/dev/null 2>&1; then
  note "${PROD_PROJECT}: missing"
  if [[ "$CHECK" == "false" ]]; then
    gcloud projects create "$PROD_PROJECT" --folder="$prod" --name="EMA Flow prod" \
      --labels=product=ema-flow,environment=prod --quiet >/dev/null ||
      die "creating ${PROD_PROJECT} failed; project ids are global, so it is probably taken.
Re-run with PROD_PROJECT_ID=<another id>. Folders and policies are in place; dev has not moved."
    echo "${PROD_PROJECT}: created, empty, no billing account"
  fi
else
  pparent="$(gcloud projects describe "$PROD_PROJECT" --format='value(parent.type,parent.id)' | tr '\t' '/')"
  [[ "$pparent" == "folder/${prod}" ]] || note "${PROD_PROJECT}: parent is ${pparent}, want folder/${prod}"
fi

# 4. Last: the dev project into non-production. `projects move` exists only in gcloud beta; the
#    v3 API call is the same operation and returns a long-running operation, polled to its end.
parent="$(gcloud projects describe "$DEV_PROJECT" --format='value(parent.type,parent.id)' | tr '\t' '/')"
if [[ "$parent" != "folder/${nonprod}" ]]; then
  note "${DEV_PROJECT}: parent is ${parent}, want folder/${nonprod}"
  if [[ "$CHECK" == "false" ]]; then
    token="$(gcloud auth print-access-token)"
    op="$(curl -fsS -X POST -H @<(ema_flow_header Authorization "Bearer ${token}") -H "Content-Type: application/json" \
      -d "{\"destinationParent\":\"folders/${nonprod}\"}" \
      "https://cloudresourcemanager.googleapis.com/v3/projects/${DEV_PROJECT}:move" |
      python3 -c "import sys,json;print(json.load(sys.stdin)['name'])")"
    result=""
    for _ in $(seq 1 60); do
      result="$(curl -fsS -H @<(ema_flow_header Authorization "Bearer ${token}") \
        "https://cloudresourcemanager.googleapis.com/v3/${op}" |
        python3 -c "import sys,json;d=json.load(sys.stdin);print('error: '+json.dumps(d['error']) if 'error' in d else ('done' if d.get('done') else ''))")"
      [[ -n "$result" ]] && break
      sleep 2
    done
    [[ "$result" == "done" ]] || die "${DEV_PROJECT}: move ${result:-did not finish in 120 s} (operation ${op})"
    now="$(gcloud projects describe "$DEV_PROJECT" --format='value(parent.id)')"
    [[ "$now" == "$nonprod" ]] || die "${DEV_PROJECT}: move reported done but parent is still ${now}"
    echo "${DEV_PROJECT}: moved to non-production"
  fi
fi

if [[ "$CHECK" == "true" ]]; then
  if [[ "$drift" == "0" ]]; then echo "No drift."; else echo "Drift found." >&2; exit 1; fi
else
  bash "$0" --check
fi
