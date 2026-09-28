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

# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../scripts/gcp" && pwd)/common.sh"
ema_flow_option --check "$@"
# GOOGLE_CLOUD_PROJECT or GCP_PROJECT_ID (refused when the two differ), else the gcloud
# configuration; no project at all fails rather than falling back to a hard-coded one.
PROJECT_ID="$(ema_flow_resolve_project)"
# No default: an agent registered in the wrong Gemini Enterprise app is offered to the wrong
# people. The dev app's id is in deploy/README.md.
APP_ID="${GEMINI_APP_ID:?GEMINI_APP_ID names the Gemini Enterprise app (engine) to register in}"
AGENT_ID="verifiable_answer_agent"
AUTHORIZATION_ID="query_service_bearer_token"
BASE="https://discoveryengine.googleapis.com/v1alpha"

PROJECT_NUMBER="$(gcloud --quiet projects describe "$PROJECT_ID" --format='value(projectNumber)')"
PARENT="projects/${PROJECT_NUMBER}/locations/global/collections/default_collection/engines/${APP_ID}/assistants/default_assistant"
NAME="${PARENT}/agents/${AGENT_ID}"
TOKEN="$(gcloud --quiet auth print-access-token)"
hdr=(--header "X-Goog-User-Project: ${PROJECT_ID}" --header "Content-Type: application/json")
work="$(mktemp -d)"
register_cleanup() { rm -rf "$work"; }
ema_flow_on_exit register_cleanup

# The access token reaches curl on its standard input, as a config line, never as an argument:
# an argument is visible to every process on the machine for the life of the call.
google_curl() {
  printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | curl --config - --silent --show-error "$@"
}

# Registered (200), not registered (404), or a failure to say which — never guessed from a body.
code="$(google_curl --output "${work}/current.json" --write-out '%{http_code}' "${hdr[@]}" \
  "${BASE}/${NAME}")" || code="curl-failed"
case "$code" in
  200) exists="yes" ;;
  404) exists="no" ;;
  *)
    echo "reading the registration failed (HTTP ${code}): $(cat "${work}/current.json" 2>/dev/null)" >&2
    exit 1
    ;;
esac
current="$(cat "${work}/current.json")"
if [[ "$EMA_FLOW_OPTION" == "--check" ]]; then
  if [[ "$exists" == "yes" ]]; then
    python3 -c "import sys,json;d=json.loads(sys.argv[1]);print('registered:',d['name'].split('/')[-1],'->',d.get('adkAgentDefinition',{}).get('provisionedReasoningEngine',{}).get('reasoningEngine'),'state',d.get('state'),'auth',d.get('authorizationConfig'))" "$current"
    ema_flow_finish
  fi
  echo "agent ${AGENT_ID}: not registered" >&2
  exit 1
fi

: "${AGENT_RESOURCE:?AGENT_RESOURCE names the reasoning engine deploy_agent_engine.py printed}"
# Built by Python into a private file, not inside a command substitution: bash mis-parses a
# heredoc containing parentheses there, which silently truncated this request on 2026-09-22.
body_file="${work}/body.json"
NAME="$NAME" ENGINE="$AGENT_RESOURCE" NUMBER="$PROJECT_NUMBER" AUTH="$AUTHORIZATION_ID" python3 -c '
import json, os
number, auth = os.environ["NUMBER"], os.environ["AUTH"]
print(json.dumps({
    "name": os.environ["NAME"],
    "displayName": "Verifiable answers (EMA Flow)",
    # Only what the agent does. Until 2026-09-27 this said it "machine-checks every quotation"
    # (it checks the label sections it shows, not quotations in its own words) and "refuses
    # questions about products the user is not entitled to" (the store answers such documents as
    # not found; nothing refuses the question) — audit AG-2.
    "description": (
        "Answers questions about approved medicinal product information from the verified "
        "label store only. Shows the label sections it read verbatim, with the document "
        "version, section and checksum, each re-checked against the store before it is shown; "
        "its own remarks are labelled as its own and are not checked. Does not search the web "
        "and does not summarise from memory. Reads only documents the signed-in user is "
        "entitled to; any other is answered as not found."
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
                "Answers only from the verified ePI store, showing the label sections verbatim "
                "with the document version and a content hash."
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
  verb=PATCH
  url="${BASE}/${NAME}?updateMask=displayName,description,adkAgentDefinition,authorizationConfig,sharingConfig"
else
  verb=POST
  url="${BASE}/${PARENT}/agents?agentId=${AGENT_ID}"
fi
# --fail-with-body: an error answer stops the script, with the answer shown.
response="$(google_curl --fail-with-body --request "$verb" "${hdr[@]}" \
  --data-binary "@${body_file}" "$url")" || {
  echo "registration failed: ${response}" >&2
  exit 1
}
python3 -c "import sys,json;print('registered:',json.loads(sys.argv[1])['name'])" "$response"
ema_flow_finish
