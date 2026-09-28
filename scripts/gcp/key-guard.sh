#!/usr/bin/env bash
# No ordinary identity can destroy, disable or ungrant an encryption key
# (docs/design/cmek-rollout.md, "Protection against losing a key", point 3).
#
# A Cloud Healthcare dataset whose key becomes unavailable is disabled after one hour and deleted,
# with every store in it, after 30 days. Terraform's prevent_destroy stops an apply destroying a
# key, but not a person or an identity calling Cloud KMS directly. This IAM deny policy does: deny
# rules are evaluated before any grant, so even an owner is refused.
#
#   - Nobody may destroy a key version, or update one — updating is how a version is disabled.
#   - Nobody but the deployer may change a key's or key ring's IAM policy. The deployer manages the
#     service agents' grants through reviewed Terraform; anyone else removing a grant would make a
#     key unavailable to its service just as surely as disabling it.
#
# Deny policies need roles/iam.denyAdmin, which the deploy identity does not and should not hold,
# so this is applied by the organisation administrator, outside the unattended deploy — like the
# deploy identity's condition in deploy-identity.sh. Break-glass is deleting this policy, which is
# itself an audited act by an administrator:
#   gcloud iam policies delete key-guard --attachment-point="$ATTACHMENT" --kind=denypolicies
#
#   bash scripts/gcp/key-guard.sh            # create or update
#   bash scripts/gcp/key-guard.sh --check    # report only; exit 1 if missing or different
set -euo pipefail

# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
ema_flow_option --check "$@"
# GOOGLE_CLOUD_PROJECT or GCP_PROJECT_ID (refused when the two differ), else the gcloud
# configuration; no project at all fails rather than falling back to a hard-coded one.
PROJECT_ID="$(ema_flow_resolve_project)"
DEPLOYER="ema-flow-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
POLICY_ID="key-guard"
ATTACHMENT="cloudresourcemanager.googleapis.com%2Fprojects%2F${PROJECT_ID}"

policy="$(mktemp)"
key_guard_cleanup() { rm -f "$policy"; }
ema_flow_on_exit key_guard_cleanup
cat > "$policy" <<JSON
{
  "displayName": "Protect the record's encryption keys",
  "rules": [
    {
      "description": "Nobody may destroy or disable a key version.",
      "denyRule": {
        "deniedPrincipals": ["principalSet://goog/public:all"],
        "deniedPermissions": [
          "cloudkms.googleapis.com/cryptoKeyVersions.destroy",
          "cloudkms.googleapis.com/cryptoKeyVersions.update"
        ]
      }
    },
    {
      "description": "Only the deployer, through reviewed Terraform, may change key grants.",
      "denyRule": {
        "deniedPrincipals": ["principalSet://goog/public:all"],
        "exceptionPrincipals": ["principal://iam.googleapis.com/projects/-/serviceAccounts/${DEPLOYER}"],
        "deniedPermissions": [
          "cloudkms.googleapis.com/cryptoKeys.setIamPolicy",
          "cloudkms.googleapis.com/keyRings.setIamPolicy"
        ]
      }
    }
  ]
}
JSON

current="$(gcloud iam policies get "$POLICY_ID" --attachment-point="$ATTACHMENT" \
  --kind=denypolicies --format=json 2>/dev/null || true)"

same="$(python3 - "$policy" "$current" <<'PY'
import json, sys
want = json.load(open(sys.argv[1]))
have_text = sys.argv[2]
if not have_text.strip():
    print("missing"); raise SystemExit
have = json.loads(have_text)
norm = lambda rules: sorted(json.dumps(r.get("denyRule", {}), sort_keys=True) for r in rules)
print("same" if norm(have.get("rules", [])) == norm(want["rules"]) else "different")
PY
)"
echo "key-guard deny policy: ${same}"

if [[ "$same" == "same" ]]; then ema_flow_finish; fi
if [[ "$EMA_FLOW_OPTION" == "--check" ]]; then exit 1; fi

if [[ "$same" == "missing" ]]; then
  gcloud iam policies create "$POLICY_ID" --attachment-point="$ATTACHMENT" \
    --kind=denypolicies --policy-file="$policy"
else
  etag="$(python3 -c 'import json,sys;print(json.loads(sys.argv[1]).get("etag",""))' "$current")"
  python3 - "$policy" "$etag" <<'PY'
import json, sys
p = json.load(open(sys.argv[1])); p["etag"] = sys.argv[2]
json.dump(p, open(sys.argv[1], "w"))
PY
  gcloud iam policies update "$POLICY_ID" --attachment-point="$ATTACHMENT" \
    --kind=denypolicies --policy-file="$policy"
fi
bash "$0" --check
ema_flow_finish
