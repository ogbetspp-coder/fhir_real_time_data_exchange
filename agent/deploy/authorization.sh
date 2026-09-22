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

echo "project ${PROJECT_ID} (${PROJECT_NUMBER}); authorization ${AUTHORIZATION_ID} is missing, so it will be created."
read -r -s -p "Paste the OAuth client secret for ${CLIENT_ID} and press Enter (nothing will show): " CLIENT_SECRET
echo
echo "secret received (${#CLIENT_SECRET} characters)"
[[ -n "$CLIENT_SECRET" ]] || { echo "No secret entered." >&2; exit 1; }

# The request body goes to a private temporary file, built by Python reading the secret from its
# environment: not a command-line argument, which other processes can briefly see, and not a
# heredoc inside a command substitution, which bash mis-parsed on the first run (2026-09-22).
body_file="$(mktemp)"
chmod 600 "$body_file"
trap 'rm -f "$body_file"' EXIT
NAME="$NAME" CLIENT_ID="$CLIENT_ID" CLIENT_SECRET="$CLIENT_SECRET" python3 -c '
import json, os
client_id = os.environ["CLIENT_ID"]
print(json.dumps({
    "name": os.environ["NAME"],
    "displayName": "EMA Flow query service, as the end user",
    "serverSideOauth2": {
        "clientId": client_id,
        "clientSecret": os.environ["CLIENT_SECRET"],
        "authorizationUri": "https://accounts.google.com/o/oauth2/v2/auth?client_id=" + client_id
        + "&response_type=code&access_type=offline&prompt=consent&scope=openid%20email%20profile",
        "tokenUri": "https://oauth2.googleapis.com/token",
    },
}))' >"$body_file"
unset CLIENT_SECRET

if [[ "$status" == "200" ]]; then
  verb=PATCH; url="${BASE}/${NAME}"
else
  verb=POST; url="${BASE}/projects/${PROJECT_NUMBER}/locations/global/authorizations?authorizationId=${AUTHORIZATION_ID}"
fi
echo "sending ${verb} to the Discovery Engine API…"
response_file="$(mktemp)"
trap 'rm -f "$body_file" "$response_file"' EXIT
code="$(curl --silent --show-error --output "$response_file" --write-out '%{http_code}' --request "$verb" \
  --header "Authorization: Bearer ${TOKEN}" --header "X-Goog-User-Project: ${PROJECT_ID}" \
  --header "Content-Type: application/json" --data-binary "@${body_file}" "$url")" || code="curl-failed"
rm -f "$body_file"
echo "HTTP ${code}"
# Only the name, or the error, is printed: the response echoes the client id, never the secret.
python3 -c "
import sys,json
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    print('no JSON in the response'); sys.exit(1)
if 'error' in d:
    print('error:',d['error'].get('status'),d['error'].get('message')); sys.exit(1)
print('authorization created:',d['name'])" "$response_file"
bash "$0" --check
