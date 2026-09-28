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
# The client secret is never a command-line argument and is never printed or written by this
# script: it is read from the clipboard, from Google's client_secret JSON download or a file
# holding it alone, or from a hidden prompt (the modes below), and sent once to the API, which
# stores it encrypted. Run by the owner: it is the same consent screen decision as the
# connector's client.
#
# The OAuth client must list BOTH redirect URIs, or the consent window never closes:
#   https://vertexaisearch.cloud.google.com/oauth-redirect          (the MCP connector)
#   https://vertexaisearch.cloud.google.com/static/oauth/oauth.html (an agent authorization)
#
#   bash agent/deploy/authorization.sh --clipboard           # copy it in the console, then run
#   bash agent/deploy/authorization.sh client_secret_*.json  # or read Google's download
#   bash agent/deploy/authorization.sh --secret-file PATH    # or a file holding it alone
#   bash agent/deploy/authorization.sh                       # or a hidden prompt
#   bash agent/deploy/authorization.sh --check               # report only; exit 1 if missing
set -euo pipefail

# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../scripts/gcp" && pwd)/common.sh"
# The command lines above, and nothing else, checked before anything is read (review round 3):
# a secret file must exist, and --help prints this header.
case "$#:${1:-}" in
  0: | "1:--check" | "1:--clipboard") ;;
  1:--help | 1:-h) ema_flow_help "${BASH_SOURCE[0]}" ;;
  2:--secret-file) ;;
  1:-*) ema_flow_refuse "${BASH_SOURCE[0]}" "[--check|--clipboard|--secret-file PATH|PATH]" "$@" ;;
  1:*) [[ -f "$1" ]] || ema_flow_refuse "${BASH_SOURCE[0]}" "[--check|--clipboard|--secret-file PATH|PATH]" "$@" ;;
  *) ema_flow_refuse "${BASH_SOURCE[0]}" "[--check|--clipboard|--secret-file PATH|PATH]" "$@" ;;
esac
# GOOGLE_CLOUD_PROJECT or GCP_PROJECT_ID (refused when the two differ), else the gcloud
# configuration; no project at all fails rather than falling back to a hard-coded one.
PROJECT_ID="$(ema_flow_resolve_project)"
AUTHORIZATION_ID="query_service_bearer_token"
# No default: the connector's OAuth client is a per-tenant console decision. The dev client's id
# is in deploy/README.md. Required below, once --check (which does not use it) has returned.
CLIENT_ID="${GEMINI_OAUTH_CLIENT_ID:-}"
BASE="https://discoveryengine.googleapis.com/v1alpha"

PROJECT_NUMBER="$(gcloud --quiet projects describe "$PROJECT_ID" --format='value(projectNumber)')"
NAME="projects/${PROJECT_NUMBER}/locations/global/authorizations/${AUTHORIZATION_ID}"
TOKEN="$(gcloud --quiet auth print-access-token)"

# The access token reaches curl on its standard input, as a config line, never as an argument:
# an argument is visible to every process on the machine for the life of the call.
google_curl() {
  printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" | curl --config - --silent --show-error "$@"
}

status="$(google_curl --output /dev/null --write-out '%{http_code}' \
  --header "X-Goog-User-Project: ${PROJECT_ID}" "${BASE}/${NAME}")" || status="curl-failed"
if [[ "${1:-}" == "--check" ]]; then
  if [[ "$status" == "200" ]]; then echo "authorization ${AUTHORIZATION_ID}: present"; ema_flow_finish; fi
  echo "authorization ${AUTHORIZATION_ID}: missing (HTTP ${status})" >&2
  exit 1
fi
# Present (200) or absent (404); anything else is a failure to find out, not "absent".
if [[ "$status" != "200" && "$status" != "404" ]]; then
  echo "reading authorization ${AUTHORIZATION_ID} failed (HTTP ${status})" >&2
  exit 1
fi

: "${CLIENT_ID:?GEMINI_OAUTH_CLIENT_ID names the OAuth client the connector uses}"
echo "project ${PROJECT_ID} (${PROJECT_NUMBER}); authorization ${AUTHORIZATION_ID} will be created or updated."

# Three ways to supply the secret, because a hidden prompt refuses a paste in some terminals:
#   bash agent/deploy/authorization.sh path/to/client_secret_....json   # the file Google gave you
#   bash agent/deploy/authorization.sh --secret-file path/to/secret.txt # a file holding only it
#   bash agent/deploy/authorization.sh                                  # hidden prompt
# The secret is never an argument, never printed, and never written by this script.
CLIENT_SECRET=""
secret_file=""
from_clipboard="false"
case "${1:-}" in
  --clipboard) from_clipboard="true" ;;
  --secret-file) secret_file="${2:?--secret-file needs a path}" ;;
  "") ;;
  *) secret_file="$1" ;;
esac

if [[ "$from_clipboard" == "true" ]]; then
  command -v pbpaste >/dev/null || { echo "pbpaste is not available; use --secret-file." >&2; exit 1; }
  # Read once, trim surrounding whitespace, and never print it. Copy the secret from the console
  # with the copy button next to it, so nothing else comes with it.
  # The console's copy button often takes surrounding page text with the value, so the secret is
  # picked out of whatever was copied rather than assumed to be all of it: exactly one
  # GOCSPX-... token must be present. Nothing read here is ever printed.
  CLIENT_SECRET="$(pbpaste | python3 -c '
import re, sys
found = sorted(set(re.findall(r"GOCSPX-[A-Za-z0-9_-]{20,}", sys.stdin.read())))
if len(found) == 1:
    print(found[0])
elif not found:
    sys.exit("no GOCSPX- secret found in the clipboard")
else:
    sys.exit(f"{len(found)} different secrets found in the clipboard; copy only one")
')" || { echo "Copy just the secret value with the console's copy button, then run this again." >&2; exit 1; }
  echo "secret found in the clipboard (${#CLIENT_SECRET} characters)"
fi

if [[ "$from_clipboard" == "true" ]]; then
  :
elif [[ -n "$secret_file" ]]; then
  [[ -f "$secret_file" ]] || { echo "No such file: ${secret_file}" >&2; exit 1; }
  # A Google OAuth client JSON download, or a file holding the secret alone.
  CLIENT_SECRET="$(SECRET_FILE="$secret_file" python3 -c '
import json, os, re, sys
text = open(os.environ["SECRET_FILE"], encoding="utf-8").read().strip()
try:
    data = json.loads(text)
except ValueError:
    found = sorted(set(re.findall(r"GOCSPX-[A-Za-z0-9_-]{20,}", text)))
    if len(found) != 1:
        sys.exit("that file does not hold exactly one GOCSPX- secret")
    print(found[0])
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
authorization_cleanup() { rm -f "$body_file" "${response_file:-}"; }
ema_flow_on_exit authorization_cleanup
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
  # updateMask is required: without it the API answers 200 and changes nothing, which left this
  # authorization holding its first, mangled secret through four apparently successful runs
  # (2026-09-22) while Gemini Enterprise logged "the provided client secret is invalid".
  verb=PATCH; url="${BASE}/${NAME}?updateMask=serverSideOauth2,displayName"
else
  verb=POST; url="${BASE}/projects/${PROJECT_NUMBER}/locations/global/authorizations?authorizationId=${AUTHORIZATION_ID}"
fi
echo "sending ${verb} to the Discovery Engine API…"
response_file="$(mktemp)"
# --fail-with-body: an error status fails the call, and the body still lands in the file to be
# reported below (the code is kept too; the script stops on anything but 200).
code="$(google_curl --fail-with-body --output "$response_file" --write-out '%{http_code}' \
  --request "$verb" --header "X-Goog-User-Project: ${PROJECT_ID}" \
  --header "Content-Type: application/json" --data-binary "@${body_file}" "$url")" || true
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
print('authorization:',d['name'])" "$response_file"
[[ "$code" == "200" ]] || { echo "the ${verb} was not accepted (HTTP ${code:-none})" >&2; exit 1; }

# Read it back: the response echoes what was sent, not what was stored. The redirect_uri proves
# the update applied; the secret can never be read back at all.
stored="$(google_curl --fail-with-body --header "X-Goog-User-Project: ${PROJECT_ID}" \
  "${BASE}/${NAME}")" || {
  echo "reading the authorization back failed: ${stored}" >&2
  exit 1
}
python3 -c "
import json, sys, urllib.parse
d = json.loads(sys.argv[1])
uri = (d.get('serverSideOauth2') or {}).get('authorizationUri', '')
query = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
redirect = (query.get('redirect_uri') or [''])[0]
if redirect != 'https://vertexaisearch.cloud.google.com/static/oauth/oauth.html':
    sys.exit('stored authorizationUri has no usable redirect_uri; the update did not apply')
print('stored: redirect_uri correct for client', (d.get('serverSideOauth2') or {}).get('clientId','')[:24] + '…')" "$stored"
ema_flow_finish
