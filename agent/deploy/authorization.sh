#!/usr/bin/env bash
# The Gemini Enterprise authorization the agent's tool calls run under (deploy/README.md, the
# step between 2 and 3 that Google's registration flow needs).
#
# When Gemini Enterprise routes a question to a registered ADK agent, it runs the OAuth consent
# flow for each authorization in the agent's `authorizationConfig.toolAuthorizations`, and hands
# the resulting access token to the agent in session state under `temp:<AUTHORIZATION_ID>`. The
# agent reads exactly one key, `temp:query_service_bearer_token` (tools.USER_TOKEN_STATE_KEY),
# so the authorization's id is fixed to `query_service_bearer_token` and tested against that
# constant. The token is the end user's, for the same OAuth client the MCP connector uses, so the
# query service sees the same principal, entitlement and audit identity either way.
#
# The client secret is read from the terminal, never from an argument or a file, and sent once
# to the API, which stores it encrypted. Run by the owner: it is the same consent screen decision
# as the connector's client.
#
#   bash agent/deploy/authorization.sh            # create or update
#   bash agent/deploy/authorization.sh --check    # report only; exit 1 if missing
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-sage-ship-509104-b8}"
AUTHORIZATION_ID="query_service_bearer_token"
CLIENT_ID="${GEMINI_OAUTH_CLIENT_ID:-398017980210-mgn6flks5a9nmlbkgkhh1pple9tv2075.apps.googleusercontent.com}"
BASE="https://discoveryengine.googleapis.com/v1alpha"

PROJECT_NUMBER="$(gcloud --quiet projects describe "$PROJECT_ID" --format='value(projectNumber)')"
NAME="projects/${PROJECT_NUMBER}/locations/global/authorizations/${AUTHORIZATION_ID}"
TOKEN="$(gcloud --quiet auth print-access-token)"

status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --header "Authorization: Bearer ${TOKEN}" --header "X-Goog-User-Project: ${PROJECT_ID}" "${BASE}/${NAME}")"
if [[ "${1:-}" == "--check" ]]; then
  if [[ "$status" == "200" ]]; then echo "authorization ${AUTHORIZATION_ID}: present"; exit 0; fi
  echo "authorization ${AUTHORIZATION_ID}: missing (HTTP ${status})" >&2
  exit 1
fi

read -r -s -p "OAuth client secret for ${CLIENT_ID}: " CLIENT_SECRET
echo
[[ -n "$CLIENT_SECRET" ]] || { echo "No secret entered." >&2; exit 1; }

body="$(python3 - "$NAME" "$CLIENT_ID" "$CLIENT_SECRET" <<'PY'
import json, sys
name, client_id, secret = sys.argv[1:4]
print(json.dumps({
    "name": name,
    "displayName": "EMA Flow query service, as the end user",
    "serverSideOauth2": {
        "clientId": client_id,
        "clientSecret": secret,
        "authorizationUri": "https://accounts.google.com/o/oauth2/v2/auth?client_id=" + client_id
            + "&response_type=code&access_type=offline&prompt=consent&scope=openid%20email%20profile",
        "tokenUri": "https://oauth2.googleapis.com/token",
    },
}))
PY
)"
unset CLIENT_SECRET

if [[ "$status" == "200" ]]; then
  verb=PATCH; url="${BASE}/${NAME}"
else
  verb=POST; url="${BASE}/projects/${PROJECT_NUMBER}/locations/global/authorizations?authorizationId=${AUTHORIZATION_ID}"
fi
response="$(curl --silent --show-error --request "$verb" \
  --header "Authorization: Bearer ${TOKEN}" --header "X-Goog-User-Project: ${PROJECT_ID}" \
  --header "Content-Type: application/json" --data-binary "$body" "$url")"
unset body
# Only the name is printed: the response echoes the client id, nothing more, but nothing else
# is needed.
python3 -c "import sys,json;d=json.loads(sys.argv[1]);print('error:',json.dumps(d['error'])) if 'error' in d else print('authorization:',d['name'])" "$response"
