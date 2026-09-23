#!/usr/bin/env bash
# Let Gemini Enterprise run this agent, and nothing else (deploy/README.md, step 4b).
#
# Gemini Enterprise calls a registered ADK agent as its own service agent,
# service-<project number>@gcp-sa-discoveryengine.iam.gserviceaccount.com. That agent's role,
# roles/discoveryengine.serviceAgent, does not include aiplatform.reasoningEngines.query, so the
# first real turn failed with PERMISSION_DENIED: "Reasoning Engine Execution Service stream
# failed" (2026-09-22).
#
# The obvious fix, roles/aiplatform.user on the project, carries 451 permissions including
# create and delete on every Vertex AI resource. Instead: a custom role of exactly two
# permissions — query the engine, and read it — bound on the one reasoning engine, not the
# project. Gemini can run this agent and can do nothing else in Vertex AI.
#
# Needs project IAM admin and iam.roles.create; run by the owner. Idempotent; `--check` reports.
#
#   AGENT_RESOURCE=projects/.../reasoningEngines/... bash agent/deploy/grant-invoker.sh
#   AGENT_RESOURCE=... bash agent/deploy/grant-invoker.sh --check
set -euo pipefail

# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../scripts/gcp" && pwd)/common.sh"
# GCP_PROJECT_ID first, as before; otherwise GOOGLE_CLOUD_PROJECT or the gcloud configuration,
# and no project at all fails rather than falling back to a hard-coded one.
PROJECT_ID="${GCP_PROJECT_ID:-$(ema_flow_resolve_project)}"
REGION="${GCP_REGION:-europe-west4}"
ROLE_ID="emaFlowAgentInvoker"
PERMISSIONS="aiplatform.reasoningEngines.get,aiplatform.reasoningEngines.query"
: "${AGENT_RESOURCE:?AGENT_RESOURCE names the reasoning engine, as deploy_agent_engine.py printed}"

PROJECT_NUMBER="$(gcloud --quiet projects describe "$PROJECT_ID" --format='value(projectNumber)')"
MEMBER="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com"
ROLE="projects/${PROJECT_ID}/roles/${ROLE_ID}"
API="https://${REGION}-aiplatform.googleapis.com/v1/${AGENT_RESOURCE}"
CHECK="false"
[[ "${1:-}" == "--check" ]] && CHECK="true"
TOKEN="$(gcloud --quiet auth print-access-token)"

policy="$(curl --silent --show-error --request POST --header "Authorization: Bearer ${TOKEN}" \
  --header "Content-Type: application/json" --data '{}' "${API}:getIamPolicy")"
granted="$(printf '%s' "$policy" | python3 -c "
import json, sys
policy = json.load(sys.stdin)
print(any(b.get('role') == sys.argv[1] and sys.argv[2] in b.get('members', [])
          for b in policy.get('bindings', [])))" "$ROLE" "$MEMBER")"

if [[ "$CHECK" == "true" ]]; then
  if [[ "$granted" == "True" ]]; then
    echo "Gemini Enterprise may run this agent."
    exit 0
  fi
  echo "Gemini Enterprise cannot run this agent: ${MEMBER} lacks ${ROLE} on it." >&2
  exit 1
fi

have="$(gcloud iam roles describe "$ROLE_ID" --project="$PROJECT_ID" \
  --format='value(includedPermissions)' 2>/dev/null | tr ';' ',' || true)"
if [[ -z "$have" ]]; then
  gcloud iam roles create "$ROLE_ID" --project="$PROJECT_ID" --quiet --stage=GA \
    --title="EMA Flow agent invoker" \
    --description="Run one Agent Engine agent. Granted on the agent, never the project." \
    --permissions="$PERMISSIONS" >/dev/null
  echo "role ${ROLE_ID}: created"
elif [[ "$have" != "$PERMISSIONS" ]]; then
  gcloud iam roles update "$ROLE_ID" --project="$PROJECT_ID" --quiet \
    --permissions="$PERMISSIONS" >/dev/null
  echo "role ${ROLE_ID}: permissions set"
fi

if [[ "$granted" != "True" ]]; then
  # The binding is added to the policy read above, so nothing else in it is disturbed.
  request="$(mktemp)"
  trap 'rm -f "$request"' EXIT
  printf '%s' "$policy" | python3 -c "
import json, sys
policy = json.load(sys.stdin)
policy.setdefault('bindings', []).append({'role': sys.argv[1], 'members': [sys.argv[2]]})
json.dump({'policy': policy}, open(sys.argv[3], 'w'))" "$ROLE" "$MEMBER" "$request"
  curl --silent --show-error --request POST --header "Authorization: Bearer ${TOKEN}" \
    --header "Content-Type: application/json" --data-binary "@${request}" \
    "${API}:setIamPolicy" >/dev/null
  echo "granted ${ROLE_ID} to Gemini Enterprise on this agent"
fi

bash "$0" --check
