# Shared noninteractive helpers for GCP deploy/bootstrap scripts.
# shellcheck shell=bash

export CLOUDSDK_CORE_DISABLE_PROMPTS="${CLOUDSDK_CORE_DISABLE_PROMPTS:-1}"
export CLOUDSDK_SUPPRESS_GOOG_CREDS_WARNING="${CLOUDSDK_SUPPRESS_GOOG_CREDS_WARNING:-true}"
export TF_IN_AUTOMATION="${TF_IN_AUTOMATION:-1}"
export TF_INPUT="${TF_INPUT:-0}"

# The project every script acts on: GOOGLE_CLOUD_PROJECT or GCP_PROJECT_ID, else the gcloud
# configuration. The two variables naming different projects is refused, not settled by an order
# of precedence: until audit B08 deploy.sh preferred one and record-readers.sh the other, so an
# apply ran against one project while the record's readers were narrowed in another.
ema_flow_resolve_project() {
  local google="${GOOGLE_CLOUD_PROJECT:-}" gcp="${GCP_PROJECT_ID:-}" project
  if [[ -n "$google" && -n "$gcp" && "$google" != "$gcp" ]]; then
    echo "GOOGLE_CLOUD_PROJECT (${google}) and GCP_PROJECT_ID (${gcp}) name different projects. Set one of them, or both to the same project." >&2
    exit 1
  fi
  project="${google:-$gcp}"
  if [[ -z "$project" || "$project" == "(unset)" ]]; then
    project="$(gcloud --quiet config get-value project 2>/dev/null || true)"
  fi
  if [[ -z "$project" || "$project" == "(unset)" ]]; then
    echo "Set GOOGLE_CLOUD_PROJECT or GCP_PROJECT_ID, or configure a gcloud project." >&2
    exit 1
  fi
  printf '%s' "$project"
}

# The environment a script acts in, and the one project it may act on (audit B08, L1): no default
# environment, and the project must be the one scripts/gcp/environments/<environment>.env names in
# EXPECTED_PROJECT_ID. Until then deploy.sh, plan-identity.sh, storage-keys.sh and
# bq-cmek-convert.sh took an unset EMA_FLOW_ENVIRONMENT as dev, on whatever project the shell
# named. Prints the environment; exits on a refusal, so it is used as a plain assignment:
#   ENVIRONMENT="$(ema_flow_require_environment "$PROJECT_ID")"
ema_flow_require_environment() {
  local project="$1" environment="${EMA_FLOW_ENVIRONMENT:-}" file expected
  if [[ -z "$environment" ]]; then
    echo "EMA_FLOW_ENVIRONMENT names the environment to deploy (dev, validation or prod); it has no default." >&2
    exit 1
  fi
  file="scripts/gcp/environments/${environment}.env"
  if [[ ! -f "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/${file}" ]]; then
    echo "No inputs file for environment ${environment}: ${file} does not exist." >&2
    exit 1
  fi
  expected="$(sed -n 's/^EXPECTED_PROJECT_ID=//p' "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/${file}" | tail -n 1)"
  if [[ -z "$expected" ]]; then
    echo "::error title=No project for ${environment}::${file} names no EXPECTED_PROJECT_ID, so ${environment} cannot be deployed to any project." >&2
    exit 1
  fi
  if [[ "$project" != "$expected" ]]; then
    echo "::error title=Wrong project for ${environment}::${environment} is deployed to ${expected} only (${file}), not to ${project}." >&2
    exit 1
  fi
  printf '%s' "$environment"
}

# An acknowledgement that a destructive change may go ahead (audit B08, D-2; review round 1): the
# commit being deployed and a digest of exactly what would be destroyed, "<commit>:<digest>",
# which the run that refuses the change prints. A value given for one commit, or for one set of
# destroys, never lets through another: re-running an old manual run that acknowledged commit X
# re-plans X against today's state, and a different destroy there has a different digest.
#   ema_flow_destroy_digest <file of lines>     prints the digest of its sorted, unique lines
#   ema_flow_acknowledged <commit> <digest>     succeeds when ALLOW_REPLACE_ACK names both
ema_flow_destroy_digest() {
  LC_ALL=C sort -u "$1" | python3 -c "import hashlib,sys;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest()[:16])"
}
ema_flow_acknowledged() {
  [[ -n "$1" && -n "$2" && "${ALLOW_REPLACE_ACK:-}" == "$1:$2" ]]
}

ema_flow_access_token() {
  local token=""
  # Workload Identity Federation in GitHub Actions authenticates gcloud itself.
  token="$(gcloud --quiet auth print-access-token 2>/dev/null || true)"
  if [[ -z "$token" ]]; then
    token="$(gcloud --quiet auth application-default print-access-token 2>/dev/null || true)"
  fi
  if [[ -z "$token" ]]; then
    echo "Unable to obtain a Google Cloud access token." >&2
    exit 1
  fi
  printf '%s' "$token"
}

# One HTTP header line, for curl to read from a file: `curl --header @<(ema_flow_header NAME VALUE)`.
# A credential passed as an argument (a --header or --user value) is in curl's argument list,
# which any process on the host can read while the request runs; printf is a shell builtin, so
# the value written through the pipe never is.
ema_flow_header() {
  printf '%s: %s\n' "$1" "$2"
}

# A one-line summary of an HTTP response body that is safe to log: an API error's code and
# status, an OperationOutcome's issue codes and severities, and otherwise only the body's SHA-256.
# Upstream messages can quote FHIR content, so no text of the body is ever printed.
summarize_response() {
  node -e '
const fs = require("node:fs");
const crypto = require("node:crypto");
const raw = fs.readFileSync(process.argv[1]);
const digest = () =>
  `unrecognised body sha256=${crypto.createHash("sha256").update(raw).digest("hex")}`;
const token = (value, pattern) => (typeof value === "string" && pattern.test(value) ? value : "?");
let body;
try {
  body = JSON.parse(raw.toString("utf8"));
} catch {
  body = undefined;
}
if (body === null || typeof body !== "object") {
  console.log(digest());
} else if (body.error !== null && typeof body.error === "object") {
  const code = Number.isInteger(body.error.code) ? String(body.error.code) : "?";
  console.log(`code=${code} status=${token(body.error.status, /^[A-Z0-9_]{1,64}$/)}`);
} else if (body.resourceType === "OperationOutcome") {
  const issues = Array.isArray(body.issue) ? body.issue : [];
  const column = (name) =>
    issues
      .map((issue) =>
        issue === null || typeof issue !== "object" ? "?" : token(issue[name], /^[a-z-]{1,64}$/),
      )
      .join(",");
  const summary = `issues=${issues.length} codes=${column("code")} severities=${column("severity")}`;
  console.log(`OperationOutcome ${summary}`);
} else {
  console.log(digest());
}
' "$1" 2>/dev/null || echo "unrecognised body sha256=unavailable"
}
