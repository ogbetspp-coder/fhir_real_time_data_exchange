#!/usr/bin/env bash
# Register the deployed agent in the Gemini Enterprise Agent Gallery (deploy/README.md, step 3),
# through the Discovery Engine API rather than the console, so the registration is repeatable and
# its fields are in the repository.
#
# The description is what the routing model reads to decide when to send a question here; it says
# what the agent will and will not do. The authorization is the one deploy/authorization.sh
# created: Gemini Enterprise runs its consent flow and hands the end user's token to the agent.
#
#   AGENT_RESOURCE=projects/.../locations/europe-west4/reasoningEngines/... \
#     bash agent/deploy/register.sh              # create or update
#   bash agent/deploy/register.sh --check        # report the registration, exit 1 if missing
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-sage-ship-509104-b8}"
APP_ID="${GEMINI_APP_ID:-gemini-enterprise-17899354_1789935441481}"
AGENT_ID="verifiable_answer_agent"
AUTHORIZATION_ID="query_service_bearer_token"
BASE="https://discoveryengine.googleapis.com/v1alpha"

PROJECT_NUMBER="$(gcloud --quiet projects describe "$PROJECT_ID" --format='value(projectNumber)')"
PARENT="projects/${PROJECT_NUMBER}/locations/global/collections/default_collection/engines/${APP_ID}/assistants/default_assistant"
NAME="${PARENT}/agents/${AGENT_ID}"
TOKEN="$(gcloud --quiet auth print-access-token)"
hdr=(--header "Authorization: Bearer ${TOKEN}" --header "X-Goog-User-Project: ${PROJECT_ID}" --header "Content-Type: application/json")

current="$(curl --silent "${hdr[@]}" "${BASE}/${NAME}")"
exists="$(python3 -c "import sys,json;print('no' if 'error' in json.loads(sys.argv[1]) else 'yes')" "$current")"
if [[ "${1:-}" == "--check" ]]; then
  if [[ "$exists" == "yes" ]]; then
    python3 -c "import sys,json;d=json.loads(sys.argv[1]);print('registered:',d['name'].split('/')[-1],'->',d.get('adkAgentDefinition',{}).get('provisionedReasoningEngine',{}).get('reasoningEngine'),'state',d.get('state'),'auth',d.get('authorizationConfig'))" "$current"
    exit 0
  fi
  echo "agent ${AGENT_ID}: not registered" >&2
  exit 1
fi

: "${AGENT_RESOURCE:?AGENT_RESOURCE names the reasoning engine deploy_agent_engine.py printed}"
body="$(python3 - "$NAME" "$AGENT_RESOURCE" "$PROJECT_NUMBER" "$AUTHORIZATION_ID" <<'PY'
import json, sys
name, engine, number, auth = sys.argv[1:5]
print(json.dumps({
    "name": name,
    "displayName": "Verifiable answers (EMA Flow)",
    "description": (
        "Answers questions about approved medicinal product information from the verified "
        "label only. Quotes the label verbatim with the document version and section, and "
        "machine-checks every quotation before showing it. Does not search the web, does not "
        "summarise from memory, and refuses questions about products the user is not entitled to."
    ),
    "adkAgentDefinition": {"provisionedReasoningEngine": {"reasoningEngine": engine}},
    "authorizationConfig": {
        "toolAuthorizations": [f"projects/{number}/locations/global/authorizations/{auth}"]
    },
}))
PY
)"
if [[ "$exists" == "yes" ]]; then
  response="$(curl --silent --show-error --request PATCH "${hdr[@]}" --data-binary "$body" \
    "${BASE}/${NAME}?updateMask=displayName,description,adkAgentDefinition,authorizationConfig")"
else
  response="$(curl --silent --show-error --request POST "${hdr[@]}" --data-binary "$body" \
    "${BASE}/${PARENT}/agents?agentId=${AGENT_ID}")"
fi
python3 -c "import sys,json;d=json.loads(sys.argv[1]);print('error:',json.dumps(d['error'])) if 'error' in d else print('registered:',d['name'])" "$response"
