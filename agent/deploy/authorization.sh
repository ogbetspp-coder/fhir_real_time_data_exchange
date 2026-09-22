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
# The OAuth client must list BOTH redirect URIs, or the consent window never closes:
#   https://vertexaisearch.cloud.google.com/oauth-redirect          (the MCP connector)
#   https://vertexaisearch.cloud.google.com/static/oauth/oauth.html (an agent authorization)
#
#   bash agent/deploy/authorization.sh                       # hidden prompt for the secret
#   bash agent/deploy/authorization.sh client_secret_*.json  # or read it from Google's download
#   bash agent/deploy/authorization.sh --check               # report only; exit 1 if missing
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

echo "project ${PROJECT_ID} (${PROJECT_NUMBER}); authorization ${AUTHORIZATION_ID} will be created or updated."

# Three ways to supply the secret, because a hidden prompt refuses a paste in some terminals:
#   bash agent/deploy/authorization.sh path/to/client_secret_....json   # the file Google gave you
#   bash agent/deploy/authorization.sh --secret-file path/to/secret.txt # a file holding only it
#   bash agent/deploy/authorization.sh                                  # hidden prompt
# The secret is never an argument, never printed, and never written by this script.
CLIENT_SECRET=""
secret_file=""
case "${1:-}" in
  --secret-file) secret_file="${2:?--secret-file needs a path}" ;;
  "") ;;
  *) secret_file="$1" ;;
esac

if [[ -n "$secret_file" ]]; then
  [[ -f "$secret_file" ]] || { echo "No such file: ${secret_file}" >&2; exit 1; }
  # A Google OAuth client JSON download, or a file holding the secret alone.
  CLIENT_SECRET="$(SECRET_FILE="$secret_file" python3 -c '
import json, os, sys
text = open(os.environ["SECRET_FILE"], encoding="utf-8").read().strip()
try:
    data = json.loads(text)
except ValueError:
    print(text)
else:
    section = data.get("web") or data.get("installed") or data
    secret = section.get("client_secret")
    if not secret:
        sys.exit("that JSON has no client_secret")
    print(secret)')"
  echo "secret read from ${secret_file} (${#CLIENT_SECRET} characters)"
else
  read -r -s -p "Paste the OAuth client secret for ${CLIENT_ID} and press Enter (nothing will show): " CLIENT_SECRET
  echo
  echo "secret received (${#CLIENT_SECRET} characters)"
fi
[[ -n "$CLIENT_SECRET" ]] || { echo "No secret supplied." >&2; exit 1; }

# A Google OAuth client secret is one short token, "GOCSPX-" and about thirty more characters.
# Anything else is a mangled paste: on 2026-09-22 a paste carried the surrounding instructions
# with it, so 217 and then 319 characters were sent to Google as the secret, the consent flow
# could never succeed, and the trailing lines of the paste ran as shell commands — which is how
# a live secret came to be echoed into a terminal. Refused here rather than sent.
if [[ "$CLIENT_SECRET" != GOCSPX-* || ${#CLIENT_SECRET} -gt 64 || "$CLIENT_SECRET" == *[[:space:]]* ]]; then
  echo "That does not look like a Google OAuth client secret (${#CLIENT_SECRET} characters)." >&2
  echo "It should start GOCSPX- and be about 35 characters, with nothing else pasted with it." >&2
  echo "If a secret was echoed into your terminal, rotate it in the console before using it." >&2
  echo "Easier: pass the file Google gave you, which needs no paste at all:" >&2
  echo "  bash agent/deploy/authorization.sh ~/Downloads/client_secret_<id>.json" >&2
  exit 1
fi

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
        # Exactly the template Google documents for an agent authorization. The redirect_uri is
        # fixed and must also be listed on the OAuth client: without it the consent window opens,
        # the user signs in, and the window never closes, because the result has nowhere to go
        # (observed 2026-09-22).
        "authorizationUri": (
            "https://accounts.google.com/o/oauth2/v2/auth"
            "?client_id=" + client_id
            + "&redirect_uri=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fstatic%2Foauth%2Foauth.html"
            + "&scope=openid%20email%20profile"
            + "&include_granted_scopes=true&response_type=code&access_type=offline&prompt=consent"
        ),
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
