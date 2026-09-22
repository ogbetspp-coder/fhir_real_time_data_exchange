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
# Built by Python into a private file, not inside a command substitution: bash mis-parses a
# heredoc containing parentheses there, which silently truncated this request on 2026-09-22.
body_file="$(mktemp)"
trap 'rm -f "$body_file"' EXIT
NAME="$NAME" ENGINE="$AGENT_RESOURCE" NUMBER="$PROJECT_NUMBER" AUTH="$AUTHORIZATION_ID" python3 -c '
import json, os
number, auth = os.environ["NUMBER"], os.environ["AUTH"]
print(json.dumps({
    "name": os.environ["NAME"],
    "displayName": "Verifiable answers (EMA Flow)",
    "description": (
        "Answers questions about approved medicinal product information from the verified "
        "label only. Quotes the label verbatim with the document version and section, and "
        "machine-checks every quotation before showing it. Does not search the web, does not "
        "summarise from memory, and refuses questions about products the user is not entitled to."
    ),
    # toolDescription is what the assistant router reads when deciding whether to send a question
    # here. Without it the default assistant answered by itself, twice, inventing a version id
    # and hashes rather than calling a tool (2026-09-22).
    "adkAgentDefinition": {
        "provisionedReasoningEngine": {"reasoningEngine": os.environ["ENGINE"]},
        "toolSettings": {
            "toolDescription": (
                "Use for any question about the content of an approved medicinal product label "
                "or product information: warnings, contraindications, dosage, a numbered section "
                "of an SmPC or package leaflet, or what a particular version of a label says. "
                "Answers only from the verified ePI store, quoting verbatim with the document "
                "version and a content hash."
            )
        },
    },
    # Offered to every user of the app, as the built-in agents are.
    "sharingConfig": {"scope": "ALL_USERS"},
    "authorizationConfig": {
        "toolAuthorizations": [f"projects/{number}/locations/global/authorizations/{auth}"]
    },
}))' >"$body_file"

if [[ "$exists" == "yes" ]]; then
  response="$(curl --silent --show-error --request PATCH "${hdr[@]}" --data-binary "@${body_file}" \
    "${BASE}/${NAME}?updateMask=displayName,description,adkAgentDefinition,authorizationConfig,sharingConfig")"
else
  response="$(curl --silent --show-error --request POST "${hdr[@]}" --data-binary "@${body_file}" \
    "${BASE}/${PARENT}/agents?agentId=${AGENT_ID}")"
fi
python3 -c "import sys,json;d=json.loads(sys.argv[1]);print('error:',json.dumps(d['error'])) if 'error' in d else print('registered:',d['name'])" "$response"
