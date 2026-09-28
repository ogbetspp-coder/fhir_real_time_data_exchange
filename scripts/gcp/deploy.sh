#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
cd "$ROOT"

PHASE="${1:-all}"
trap 'echo "::error title=Phase ${PHASE} failed::${BASH_COMMAND} exited $?"' ERR
# Temporary files that must not outlive the script however it ends (a saved plan holds sensitive
# values); plan_reviewed adds to it.
DEPLOY_TEMP_FILES=()
# The script's own status is kept: an exit caused by an expansion error (an unset
# EMA_FLOW_ENVIRONMENT) would otherwise end with the status of this cleanup, 0.
trap 'deploy_status=$?; rm -f ${DEPLOY_TEMP_FILES[@]+"${DEPLOY_TEMP_FILES[@]}"}; exit "$deploy_status"' EXIT
PROJECT_ID="$(ema_flow_resolve_project)"
# Exported unconditionally, both names, so every script this one runs (record-readers.sh,
# bootstrap.sh, reconcile-fhir-stores.sh) acts on the project this one resolved.
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export GCP_PROJECT_ID="$PROJECT_ID"
export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"
REGION="${GCP_REGION:-europe-west4}"
# No default (audit B08, L1): until then an unset or empty EMA_FLOW_ENVIRONMENT read as dev, and
# dev's inputs (alerts that page no one, synthetic sources accepted) applied to whatever project
# the shell named.
if [[ -z "${EMA_FLOW_ENVIRONMENT:-}" ]]; then
  echo "EMA_FLOW_ENVIRONMENT names the environment to deploy (dev, validation or prod); it has no default." >&2
  exit 1
fi
ENVIRONMENT="$EMA_FLOW_ENVIRONMENT"
export EMA_FLOW_ENVIRONMENT="$ENVIRONMENT"

# The environment's own inputs (QUERY_LOG_REJECTION_REASON, ALLOW_SYNTHETIC_SOURCES,
# REQUIRE_ALERT_RECIPIENT, EXPECTED_PROJECT_ID), set in one file that the pull-request plan and
# the deploy both read through this script, so the plan is what applies. A missing file is
# refused rather than read as "all defaults". Each is cleared first, so an input the file leaves
# unset takes its default and never a value from the caller's shell: prod.env sets nothing, and
# ALLOW_SYNTHETIC_SOURCES=true left exported in a shell must not reach a prod deploy.
ENVIRONMENT_INPUTS="${ROOT}/scripts/gcp/environments/${ENVIRONMENT}.env"
if [[ ! -f "$ENVIRONMENT_INPUTS" ]]; then
  echo "No inputs file for environment ${ENVIRONMENT}: ${ENVIRONMENT_INPUTS#"${ROOT}/"} does not exist." >&2
  exit 1
fi
unset QUERY_LOG_REJECTION_REASON ALLOW_SYNTHETIC_SOURCES REQUIRE_ALERT_RECIPIENT EXPECTED_PROJECT_ID
# shellcheck source=/dev/null
source "$ENVIRONMENT_INPUTS"

# Each environment names the one project it may be deployed to (EXPECTED_PROJECT_ID), and any
# other is refused: the environment and the project come from different places (a workflow's env,
# a repository variable, an operator's shell), and nothing else ties them together. An environment
# that names no project yet cannot be deployed anywhere. common.sh does the check, for the
# operator scripts too.
ema_flow_require_environment "$PROJECT_ID" >/dev/null

# Which commit is being deployed, named the same way in the image tag (TAG), in every audit record
# and run manifest (SERVICE_VERSION: QUERY_SERVICE_VERSION and GIT_COMMIT), and in each image's
# org.opencontainers.image.revision label (audit B08, D-4). Both come from one `git rev-parse
# HEAD`, found with `git rev-parse --git-dir`, which a worktree answers too (its .git is a file,
# and the `-d .git` test this replaced sent every worktree deploy to the shared tag "manual").
#
# The image build (phase_images) uploads the working tree, not the commit, so a tree that differs
# from HEAD -- a changed tracked file, or an untracked one .gitignore does not exclude, which the
# upload honours too -- is not that commit. Both names then carry -dirty-<tree>: the id of the
# tree the working copy would commit as, so the same edits give the same name and different edits
# never share one. Without git, GITHUB_SHA names the commit; with neither, nothing is built or applied
# (require_provenance). In Actions, a checkout whose HEAD is not GITHUB_SHA is refused the same way.
deploy_provenance() {
  local commit="" suffix="" index tree
  PROVENANCE_ERROR=""
  if git rev-parse --git-dir >/dev/null 2>&1; then
    commit="$(git rev-parse HEAD)"
    # A scratch index read from HEAD, so every file is hashed afresh rather than trusted by its
    # timestamp, and the repository's own index is left alone.
    index="$(mktemp -d)"
    tree="$(GIT_INDEX_FILE="${index}/index" git read-tree HEAD 2>/dev/null &&
      GIT_INDEX_FILE="${index}/index" git add -A >/dev/null 2>&1 &&
      GIT_INDEX_FILE="${index}/index" git write-tree 2>/dev/null || true)"
    rm -rf "$index"
    if [[ -z "$tree" ]]; then
      PROVENANCE_ERROR="could not read the working tree to compare it with ${commit}"
    elif [[ "$tree" != "$(git rev-parse 'HEAD^{tree}')" ]]; then
      suffix="-dirty-${tree:0:12}"
    fi
    if [[ -n "${GITHUB_SHA:-}" && "$GITHUB_SHA" != "$commit" ]]; then
      PROVENANCE_ERROR="the checkout is at ${commit}, not GITHUB_SHA ${GITHUB_SHA}"
    fi
  elif [[ -n "${GITHUB_SHA:-}" ]]; then
    commit="$GITHUB_SHA"
  else
    PROVENANCE_ERROR="this is not a git checkout and GITHUB_SHA is not set, so no commit names what would be built"
  fi
  if [[ -z "$commit" ]]; then
    # "local" is the Terraform default for an apply outside this script; nothing is built or
    # applied under it (require_provenance).
    TAG="local"
    SERVICE_VERSION="local"
  else
    TAG="${commit:0:12}${suffix}"
    SERVICE_VERSION="${commit}${suffix}"
  fi
}

# Names the commit, and stops a phase that builds or applies when it cannot.
require_provenance() {
  deploy_provenance
  if [[ -n "$PROVENANCE_ERROR" ]]; then
    echo "::error title=Deploy provenance::Not building or applying: ${PROVENANCE_ERROR}." >&2
    exit 1
  fi
  # In Actions the checkout is the commit, so a tree that differs from it is something this job
  # changed: refused rather than built and named -dirty.
  if [[ "$SERVICE_VERSION" == *-dirty-* && "${GITHUB_ACTIONS:-}" == "true" ]]; then
    echo "::error title=Deploy provenance::The checkout differs from ${SERVICE_VERSION%%-dirty-*}: a step of this job changed it. Not building or applying." >&2
    exit 1
  fi
  if [[ "$SERVICE_VERSION" == *-dirty-* ]]; then
    echo "::warning title=Uncommitted changes::The working tree differs from ${SERVICE_VERSION%%-dirty-*}; images, audit records and run manifests name ${SERVICE_VERSION}."
  fi
}

# The image repository: named once, here. It is encrypted with the `artifacts` key
# (infra/main.tf, google_artifact_registry_repository.images_cmek; CMEK step 4). The builds, the
# digest lookups and the Cloud Run image references all take it from this variable.
REPOSITORY_ID="ema-flow-images"
REPOSITORY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY_ID}"

tf_common_vars=(
  -var="project_id=${PROJECT_ID}"
  -var="region=${REGION}"
  -var="environment=${ENVIRONMENT}"
)

# Converts a comma-separated environment value into the JSON array Terraform's -var flag parses
# as list(string): "a, b" becomes ["a","b"], empty becomes []. A value that already starts with
# "[" is passed through unchanged, so JSON can be supplied directly. Surrounding whitespace is
# stripped from each element, empty elements are dropped, and " and \ inside an element are
# escaped.
ema_flow_json_array() {
  local raw="${1:-}"
  if [[ "$raw" == \[* ]]; then
    printf '%s' "$raw"
    return 0
  fi
  local -a parts=()
  IFS=',' read -r -a parts <<<"$raw" || true
  local out="[" first=1 item
  # ${parts[@]+...}: reading an empty value leaves parts unset in bash 3.2 (macOS), where
  # expanding an unset array under `set -u` is an error.
  for item in ${parts[@]+"${parts[@]}"}; do
    item="${item#"${item%%[![:space:]]*}"}"
    item="${item%"${item##*[![:space:]]}"}"
    if [[ -z "$item" ]]; then
      continue
    fi
    item="${item//\\/\\\\}"
    item="${item//\"/\\\"}"
    if [[ "$first" == 1 ]]; then first=0; else out+=","; fi
    out+="\"${item}\""
  done
  printf '%s]' "$out"
}

# The full -var list the deploy applies with, built in one place so the pull-request plan
# (phase_plan) and the apply cannot drift apart. Sets TF_DEPLOY_VARS.
#   tf_deploy_vars <deployer account> <worker image> <validator image> <query image> <service version>
tf_deploy_vars() {
  # Query service access configuration, supplied by the environment (GitHub Actions repository
  # variables, see .github/workflows/deploy.yml). Unset means the Terraform defaults: no
  # invoker, no token creator on the caller service account, no entitlement, no accepted OAuth
  # client id -- a service that deploys and passes its startup probe while authorising no
  # caller, rather than a deploy that fails.
  local query_invokers_json query_token_creators_json query_oauth_client_ids_json query_entitlements_json
  query_invokers_json="$(ema_flow_json_array "${QUERY_INVOKERS:-}")"
  query_token_creators_json="$(ema_flow_json_array "${QUERY_TOKEN_CREATORS:-}")"
  query_oauth_client_ids_json="$(ema_flow_json_array "${QUERY_OAUTH_CLIENT_IDS:-}")"
  query_entitlements_json="${QUERY_ENTITLEMENTS_JSON:-}"
  if [[ -z "$query_entitlements_json" ]]; then
    query_entitlements_json='{}'
  fi
  # Sizes, not values: invoker members, token-creator members and entitlement keys are account
  # identifiers, and this log is attached to a failure issue by .github/workflows/deploy.yml.
  echo "query access configuration: query_invokers=${#query_invokers_json} bytes, query_token_creators=${#query_token_creators_json} bytes, query_oauth_client_ids=${#query_oauth_client_ids_json} bytes, query_entitlements_json=${#query_entitlements_json} bytes (2 bytes is the empty default)"

  # Who every alert pages (infra/variables.tf `alert_notification_email` and
  # `alert_notification_channels`): an encryption key made unavailable (one hour before the FHIR
  # dataset is disabled), a failed pipeline run, and entitlement probing. Required: with neither,
  # this refuses here, before Terraform, and Terraform's preconditions refuse too. Until
  # 2026-09-27 both were optional and a deploy without them silently dropped the key alert.
  # The one exception is dev (owner decision, 2026-09-28): its inputs file sets
  # REQUIRE_ALERT_RECIPIENT=false, and its alert policies are created but page no one. Any other
  # environment is refused that setting, here and by infra/variables.tf, wherever it came from.
  # Presence and counts only are logged: an address is personal data and this log can be
  # attached to a failure issue.
  local alert_notification_email="${ALERT_NOTIFICATION_EMAIL:-}" alert_notification_channels_json
  local require_alert_recipient="${REQUIRE_ALERT_RECIPIENT:-true}"
  alert_notification_channels_json="$(ema_flow_json_array "${ALERT_NOTIFICATION_CHANNELS:-}")"
  if [[ "$require_alert_recipient" != "true" && "$require_alert_recipient" != "false" ]]; then
    echo "::error title=Invalid alert setting::REQUIRE_ALERT_RECIPIENT must be true or false." >&2
    return 1
  fi
  if [[ "$require_alert_recipient" == "false" && "${ENVIRONMENT:-}" != "dev" ]]; then
    echo "::error title=Alert recipient required::REQUIRE_ALERT_RECIPIENT=false is accepted only in dev; ${ENVIRONMENT:-this environment}'s alerts must page someone." >&2
    return 1
  fi
  if [[ "$require_alert_recipient" == "true" && -z "$alert_notification_email" && "$alert_notification_channels_json" == "[]" ]]; then
    echo "::error title=No alert recipient::Set ALERT_NOTIFICATION_EMAIL (a repository variable in GitHub Actions) or ALERT_NOTIFICATION_CHANNELS. Every alert policy must page someone; none is deployed without." >&2
    return 1
  fi
  # A placeholder is no recipient either: an address in a domain reserved so that nothing is ever
  # delivered there (RFC 2606: example.com/.org/.net, and the .test, .invalid, .example and
  # .localhost top-level domains, with or without a trailing dot), or a `you@` local part left
  # from a template, pages nobody, however well-formed. The address is not printed;
  # infra/variables.tf refuses the same shapes.
  if [[ -n "$alert_notification_email" ]] &&
    printf '%s' "$alert_notification_email" | grep -Eiq '^you@|@([^@]+\.)?example\.(com|org|net)\.?$|[@.](test|invalid|example|localhost)\.?$'; then
    echo "::error title=Placeholder alert recipient::ALERT_NOTIFICATION_EMAIL is a placeholder (a reserved example or test domain, or a you@ address). Set it to a real, watched address." >&2
    return 1
  fi
  echo "alert configuration: alert_notification_email is $([[ -n "$alert_notification_email" ]] && echo set || echo 'not set'), alert_notification_channels=${#alert_notification_channels_json} bytes, require_alert_recipient=${require_alert_recipient}"
  if [[ -z "$alert_notification_email" && "$alert_notification_channels_json" == "[]" ]]; then
    echo "::notice title=Alerts page no one::${ENVIRONMENT:-This environment} has no alert recipient: its alert policies are created but notify nobody. Before production or any real data, set a real, monitored address."
  fi

  TF_DEPLOY_VARS=(
    "${tf_common_vars[@]}"
    -var="deployer_account=${1}"
    -var="service_version=${5}"
    -var="worker_image=${2}"
    -var="validator_image=${3}"
    -var="query_image=${4}"
    -var="query_invokers=${query_invokers_json}"
    -var="query_token_creators=${query_token_creators_json}"
    -var="query_oauth_client_ids=${query_oauth_client_ids_json}"
    -var="query_entitlements_json=${query_entitlements_json}"
    -var="alert_notification_email=${alert_notification_email}"
    -var="alert_notification_channels=${alert_notification_channels_json}"
    -var="require_alert_recipient=${require_alert_recipient}"
    # Dev logs why a credential was refused (a category, never the token); production does not.
    # Set by the environment's inputs file (scripts/gcp/environments/).
    -var="query_log_rejection_reason=${QUERY_LOG_REJECTION_REASON:-false}"
    # Whether the worker accepts synthetic content, and with it the gate-bypassing sources
    # (docs/design/authority-import-contract.md, D7). Set by the environment's inputs file; unset
    # is the Terraform default, false.
    -var="allow_synthetic_sources=${ALLOW_SYNTHETIC_SOURCES:-false}"
  )
}

phase_preflight() {
  echo "=== preflight ==="
  deploy_provenance
  echo "project=${PROJECT_ID} region=${REGION} environment=${ENVIRONMENT} tag=${TAG}"
  terraform version
  # `gcloud version` can exit 1 when component updates exist; do not fail deploy on that.
  gcloud info --format='value(basic.version)' || true
  if ! gcloud --quiet auth print-access-token >/dev/null; then
    echo "::error::Workload Identity Federation did not yield an access token. Check GCP_WORKLOAD_IDENTITY_PROVIDER, GCP_DEPLOY_SERVICE_ACCOUNT, and the WIF attribute condition for repo:ogbetspp-coder/fhir_real_time_data_exchange." >&2
    exit 1
  fi
  gcloud --quiet auth list
  if ! gcloud --quiet projects describe "$PROJECT_ID" --format='value(projectId)'; then
    echo "::warning::Could not describe project ${PROJECT_ID}. Grant the deployer SA roles/browser (resourcemanager.projects.get) if later steps fail with 403."
  fi
}

phase_deps() {
  echo "=== install node dependencies ==="
  # The lockfile or nothing: npm ci fails on a missing or out-of-date package-lock.json, and
  # there is no npm install fallback that would resolve fresh versions at deploy time.
  npm ci --no-audit --no-fund
}

# The deploy inputs, made here for a local `all` run (the workflow makes them in its gate job,
# which holds no credential; scripts/gcp/deploy-inputs.sh says why). This runs the installed
# packages, so it is never a phase of the workflow's deploy job.
phase_inputs() {
  echo "=== deploy inputs: the standards bootstrap imports, and the synthetic fixture ==="
  DEPLOY_INPUTS_DIR="$(mktemp -d)/deploy-inputs"
  DEPLOY_INPUTS_SHA256="$(bash scripts/gcp/deploy-inputs.sh "$DEPLOY_INPUTS_DIR" | tail -n 1)"
  export DEPLOY_INPUTS_DIR DEPLOY_INPUTS_SHA256
}

phase_init() {
  echo "=== terraform init ==="
  local state_bucket="${PROJECT_ID}-ema-flow-tfstate"
  if ! gcloud --quiet storage buckets describe "gs://${state_bucket}" >/dev/null 2>&1; then
    echo "Creating Terraform state bucket gs://${state_bucket}"
    gcloud --quiet storage buckets create "gs://${state_bucket}" \
      --project="$PROJECT_ID" \
      --location="$REGION" \
      --uniform-bucket-level-access \
      --public-access-prevention
    gcloud --quiet storage buckets update "gs://${state_bucket}" --versioning
  fi
  # -lockfile=readonly: the provider versions and checksums are the committed lock's, and an
  # init that would change the lock fails instead of rewriting it in the checkout it deploys.
  terraform -chdir=infra init -input=false -lockfile=readonly \
    -backend-config="bucket=${state_bucket}" \
    -backend-config="prefix=terraform/state"
  terraform -chdir=infra fmt -check -recursive
  terraform -chdir=infra validate
}

# Every `moved` block in infra/, one "from to" pair per line. A block missing either end is refused
# rather than skipped, so a pending move can never be passed over silently.
moved_pairs() {
  awk '
    /^moved \{/ { inside = 1; from = ""; to = ""; next }
    inside && /^[[:space:]]*from[[:space:]]*=/ { sub(/^[^=]*=[[:space:]]*/, ""); sub(/[[:space:]]+$/, ""); from = $0 }
    inside && /^[[:space:]]*to[[:space:]]*=/ { sub(/^[^=]*=[[:space:]]*/, ""); sub(/[[:space:]]+$/, ""); to = $0 }
    inside && /^\}/ {
      if (from == "" || to == "") { print "A moved block in " FILENAME " has no from or no to." > "/dev/stderr"; exit 1 }
      print from, to; inside = 0
    }
  ' infra/*.tf
}

# Terraform refuses a -target apply that leaves out either end of a pending `moved` block ("Moved
# resource instances excluded by targeting"), and phase_apis and sync_dashboard apply with -target.
# So a pending move is completed in the state first, as the full apply would complete it:
# `terraform state mv` changes no resource, only the address the state keeps it under. A move is
# pending while its old address, or an instance of it, is in the state; once done, its block is
# inert and this does nothing.
complete_pending_moves() {
  local state pairs from to
  state="$(terraform -chdir=infra state list)"
  pairs="$(moved_pairs)"
  while read -r from to; do
    [[ -n "$from" ]] || continue
    if awk -v address="$from" '$0 == address || index($0, address "[") == 1 { found = 1 } END { exit !found }' <<<"$state"; then
      echo "Completing the pending move ${from} -> ${to} in the Terraform state."
      terraform -chdir=infra state mv "$from" "$to"
    fi
  done <<<"$pairs"
}

# The deploy applies only a plan it has read (audit B08, D-2). Until then phase_apis and
# phase_apply ran `terraform apply -auto-approve`, and the check that a plan destroys nothing ran
# only on the pull request (.github/workflows/plan.yml): a push to main by an administrator skips
# that check, and a plan made before another merge no longer describes what merges. So each apply
# is a plan saved to a file, judged by the same summary and verdict as the pull request's
# (scripts/ci/plan-summary.py: 0 no destroy, 4 a destroy or replace, anything else an error), and
# then an apply of exactly that file (REVIEWED_PLAN), never a fresh one.
#
# A plan that destroys or replaces anything is applied only when ALLOW_REPLACE_ACK names the
# commit being deployed (SERVICE_VERSION, in full) and the digest of the addresses it destroys or
# replaces, as "<commit>:<digest>" (common.sh, ema_flow_acknowledged): the refusing run prints the
# value. An acknowledgement is for one commit's one set of destroys, and is never carried to the
# next commit, nor to a re-run whose plan destroys something else. In Actions it is the deploy
# workflow's allow_replace_ack input, given on a manual run once the destroy has been reviewed.
# Anything the verdict cannot read fails. Every temporary file here, the saved plan included
# (it holds sensitive values), is removed when the script exits, however it exits.
#   plan_reviewed <label> <terraform plan arguments...>
plan_reviewed() {
  local label="$1" plan_file plan_text plan_json summary code=0 verdict=0
  shift
  plan_file="$(mktemp)"
  plan_text="$(mktemp)"
  plan_json="$(mktemp)"
  summary="$(mktemp)"
  DEPLOY_TEMP_FILES+=("$plan_file" "$plan_text" "$plan_json" "$summary")
  echo "--- ${label}: plan ---"
  terraform -chdir=infra plan -input=false -no-color -detailed-exitcode -out="$plan_file" "$@" \
    >"$plan_text" 2>&1 || code=$?
  cat "$plan_text"
  if [[ "$code" != "1" ]] && ! terraform -chdir=infra show -json "$plan_file" >"$plan_json"; then
    code=1
  fi
  if [[ "$code" == "1" ]]; then
    rm -f "$plan_file" "$plan_text" "$plan_json" "$summary"
    echo "::error title=Plan failed::${label}: terraform could not plan; nothing was applied." >&2
    return 1
  fi
  python3 scripts/ci/plan-summary.py "$plan_json" "$plan_text" "$summary" "$code" || verdict=$?
  local destroyed digest=""
  if [[ "$verdict" == "4" ]]; then
    destroyed="$(mktemp)"
    DEPLOY_TEMP_FILES+=("$destroyed")
    python3 -c "
import json, sys
for change in json.load(open(sys.argv[1])).get('resource_changes', []):
    if 'delete' in change.get('change', {}).get('actions', []):
        print(change.get('address', '?') + (' deposed ' + change['deposed'] if change.get('deposed') else ''))
" "$plan_json" >"$destroyed" && digest="$(ema_flow_destroy_digest "$destroyed")" || digest=""
  fi
  rm -f "$plan_text" "$plan_json" "$summary"
  case "$verdict" in
    0) ;;
    4)
      deploy_provenance
      if [[ -z "$PROVENANCE_ERROR" ]] && ema_flow_acknowledged "$SERVICE_VERSION" "$digest"; then
        echo "::warning title=Destroy acknowledged::${label}: the plan destroys or replaces the resources listed above, and ALLOW_REPLACE_ACK names this commit and exactly these, so it is applied."
      else
        rm -f "$plan_file"
        echo "::error title=Destroy not acknowledged::${label}: the plan destroys or replaces the resources listed above, and nothing was applied. Once they are reviewed, deploy again with ALLOW_REPLACE_ACK=${SERVICE_VERSION}:${digest:-<digest unavailable>} (in Actions, run the deploy workflow with allow_replace_ack set to it)." >&2
        return 3
      fi
      ;;
    *)
      rm -f "$plan_file"
      echo "::error title=Plan unreadable::${label}: scripts/ci/plan-summary.py could not judge the plan (exit ${verdict}); nothing was applied." >&2
      return 1
      ;;
  esac
  REVIEWED_PLAN="$plan_file"
}

phase_apis() {
  echo "=== enable APIs and artifact registry ==="
  gcloud --quiet services enable \
    artifactregistry.googleapis.com \
    bigquery.googleapis.com \
    cloudbuild.googleapis.com \
    cloudkms.googleapis.com \
    healthcare.googleapis.com \
    iam.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com \
    pubsub.googleapis.com \
    run.googleapis.com \
    storage.googleapis.com \
    workflows.googleapis.com \
    --project="$PROJECT_ID"

  # The Artifact Registry repo can already exist in GCP (e.g. created by an earlier
  # run) without being in the current Terraform state (e.g. after the state backend
  # was lost or reset). Reconcile that drift with an import instead of failing on a
  # 409 from `apply`.
  if ! terraform -chdir=infra state show google_artifact_registry_repository.images_cmek >/dev/null 2>&1; then
    if gcloud --quiet artifacts repositories describe "$REPOSITORY_ID" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
      echo "Importing pre-existing Artifact Registry repository into Terraform state."
      terraform -chdir=infra import \
        "${tf_common_vars[@]}" \
        -var="worker_image=us-docker.pkg.dev/cloudrun/container/hello" \
        -var="validator_image=us-docker.pkg.dev/cloudrun/container/hello" \
        -var="query_image=us-docker.pkg.dev/cloudrun/container/hello" \
        google_artifact_registry_repository.images_cmek \
        "projects/${PROJECT_ID}/locations/${REGION}/repositories/${REPOSITORY_ID}"
    fi
  fi

  complete_pending_moves

  # The three placeholder images below carry no digest. The precondition on
  # google_cloud_run_v2_service.query rejects a digest-less query_image, and that resource is
  # not in the -target list, so on this apply the precondition is not evaluated. This phase has
  # not run against a project since the query service was added: that is what the -target list
  # implies, not something observed. If an apply here ever fails on that error message, the
  # -target list is reaching further than it reads.
  plan_reviewed apis \
    -target=google_project_service.required \
    -target=google_artifact_registry_repository.images_cmek \
    -target=google_service_account.build \
    -target=google_storage_bucket.build_staging \
    -target=google_storage_bucket_iam_member.build_staging_reader \
    -target=google_artifact_registry_repository_iam_member.build_writer \
    -target=google_project_iam_member.build_log_writer \
    -target=google_logging_project_bucket_config.regulated_audit \
    -target=google_logging_project_bucket_config.regulated_audit_cmek \
    -target=google_logging_project_sink.regulated_audit \
    "${tf_common_vars[@]}" \
    -var="worker_image=us-docker.pkg.dev/cloudrun/container/hello" \
    -var="validator_image=us-docker.pkg.dev/cloudrun/container/hello" \
    -var="query_image=us-docker.pkg.dev/cloudrun/container/hello"
  terraform -chdir=infra apply -input=false "$REVIEWED_PLAN"
  rm -f "$REVIEWED_PLAN"

  # google_logging_project_sink.regulated_audit's auto-provisioned writer_identity
  # isn't reliably readable back through Terraform (two separate apply passes both
  # left it empty), so grant its role imperatively instead -- the same "manage
  # outside Terraform" pattern reconcile-fhir-stores.sh already uses for the R5
  # FHIR stores. Idempotent: re-adding an existing binding is a no-op.
  local sink_writer_identity
  sink_writer_identity="$(gcloud --quiet logging sinks describe "ema-flow-${ENVIRONMENT}-regulated-audit" --project="$PROJECT_ID" --format='value(writerIdentity)')"
  if [[ -n "$sink_writer_identity" ]]; then
    gcloud --quiet projects add-iam-policy-binding "$PROJECT_ID" \
      --member="$sink_writer_identity" \
      --role="roles/logging.bucketWriter" \
      --condition=None >/dev/null
  else
    echo "::warning::Could not resolve the regulated audit log sink's writer identity; grant roles/logging.bucketWriter to it manually."
  fi
}

phase_images() {
  echo "=== cloud build images ==="
  require_provenance
  # Regional, staged in the EU, and run as the build identity (infra/build.tf,
  # docs/foundations.md A2/B3). Without these three flags gcloud defaults to a global
  # build as the default compute service account, staging the source in a US bucket.
  # The build identity's grants are made by phase_apis moments earlier and can take
  # minutes to propagate on first creation, so a transient permission-denied is retried
  # rather than failing the deploy.
  local build_account="ema-flow-build-${ENVIRONMENT}@${PROJECT_ID}.iam.gserviceaccount.com"
  local staging_dir="gs://${PROJECT_ID}-ema-flow-${ENVIRONMENT}-build-staging/source"
  local max_attempts=8
  local attempt
  local build_log
  build_log="$(mktemp)"
  for attempt in $(seq 1 "$max_attempts"); do
    if gcloud --quiet builds submit \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --service-account="projects/${PROJECT_ID}/serviceAccounts/${build_account}" \
      --gcs-source-staging-dir="$staging_dir" \
      --config=cloudbuild.images.yaml \
      --substitutions="_REGION=${REGION},_REPOSITORY=${REPOSITORY_ID},_IMAGE_TAG=${TAG},_REVISION=${SERVICE_VERSION}" \
      . 2>&1 | tee "$build_log"; then
      rm -f "$build_log"
      return 0
    fi
    # Retry only what a retry can fix: a grant made moments ago by phase_apis that has not
    # propagated yet. A failed build step — a test, a checksum, the validator refusing to start
    # offline — fails the same way every time, and rebuilding three images eight times over
    # four minutes to learn that helps nobody.
    if ! grep -Eqi "PERMISSION_DENIED|does not have permission|permission denied|403 Forbidden" "$build_log"; then
      echo "Cloud Build failed for a reason other than permission propagation; not retrying." >&2
      rm -f "$build_log"
      return 1
    fi
    if [[ "$attempt" -lt "$max_attempts" ]]; then
      echo "Cloud Build submit was refused permission (attempt ${attempt}/${max_attempts}); retrying in 30s in case the IAM grant is still propagating." >&2
      sleep 30
    fi
  done
  rm -f "$build_log"
  return 1
}

# `gcloud artifacts docker images describe` unconditionally calls Container
# Analysis to build its image_summary, needing a containeranalysis IAM role the
# deploy service account isn't granted (that account's roles are bootstrapped
# outside this repo's Terraform, see README.md) -- neither --show-package-
# vulnerability nor its --no- negation skips that call. Resolve the digest
# instead via the plain Docker Registry v2 HTTP API that Artifact Registry
# implements, which only needs the artifactregistry read access we already have.
resolve_image_digest() {
  local image_name="$1"
  local tag="$2"
  local digest token
  # Artifact Registry's docker v2 endpoint authenticates like `docker login`
  # does: HTTP Basic with the fixed username `oauth2accesstoken` and a GCP
  # access token as the password (not a raw Authorization: Bearer header).
  # The header is read from a pipe, not given as --user, which would put the token in curl's
  # argument list (common.sh, ema_flow_header).
  token="$(ema_flow_access_token)"
  digest="$(curl --fail --silent --show-error --head \
    --header @<(ema_flow_header Authorization "Basic $(printf 'oauth2accesstoken:%s' "$token" | base64 | tr -d '\n')") \
    --header "Accept: application/vnd.docker.distribution.manifest.v2+json,application/vnd.oci.image.manifest.v1+json" \
    "https://${REGION}-docker.pkg.dev/v2/${PROJECT_ID}/${REPOSITORY_ID}/${image_name}/manifests/${tag}" \
    | tr -d '\r' | grep -i '^docker-content-digest:' | awk '{print $2}')"
  if [[ -z "$digest" ]]; then
    echo "Could not resolve a digest for ${image_name}:${tag} from the registry manifest response." >&2
    exit 1
  fi
  printf '%s' "$digest"
}

# Effective IAM of the service identities, wherever the deploy can read a grant, after an apply
# (UR-18 in docs/validation/README.md, ADR 0004 decision 5; audit B08, D-3). Terraform state says
# which bindings this configuration declares; this reads what the platform holds. Until audit B08
# it read only the project and the Healthcare dataset (and later the stores), so a grant inherited
# from a folder, or held on a bucket, a BigQuery dataset or table, a key, a topic, a repository, a
# Cloud Run service or another service account, was in no export, and the deployer was not
# exported at all. It now reads every IAM policy of those kinds in the project, and the policies
# of the folders and organisation above it, and scripts/ci/effective-iam.py reports, per identity,
# every grant it holds and each role Terraform does not declare for it. The deployer's grants are
# exported and not judged: its roles are made outside Terraform (docs/architecture.md, "Grants
# outside Terraform").
#
# Evidence, never a gate: a policy that cannot be read, an undeclared role, a missing output or a
# failed upload is a warning annotation, and the function returns 0. Only the per-identity reports
# leave this function (the deploy log, and gs://<evidence bucket>/deploy-evidence/...): the raw
# policies name every other principal too, and are deleted with the Terraform state read here.
IAM_EVIDENCE_INDEX=""
IAM_EVIDENCE_COUNT=0

# Records one policy read by the command given, or that it could not be read.
#   iam_evidence_read <scope> <resource> <kind: iam | bigquery-dataset> <command...>
iam_evidence_read() {
  local scope="$1" resource="$2" kind="$3" file
  shift 3
  IAM_EVIDENCE_COUNT=$((IAM_EVIDENCE_COUNT + 1))
  file="${IAM_EVIDENCE_INDEX%/*}/policy-${IAM_EVIDENCE_COUNT}.json"
  if "$@" >"$file" 2>/dev/null; then
    printf '%s\t%s\t%s\t%s\n' "$file" "$scope" "$resource" "$kind" >>"$IAM_EVIDENCE_INDEX"
  else
    rm -f "$file"
    printf -- '-\t%s\t%s\tunread\n' "$scope" "$resource" >>"$IAM_EVIDENCE_INDEX"
  fi
}

# The resources of one kind, one per line, from the listing command given; a listing that fails
# is recorded as unread and lists nothing.
#   iam_evidence_list <scope> <command...>
iam_evidence_list() {
  local scope="$1"
  shift
  if ! "$@" 2>/dev/null; then
    printf -- '-\t%s\t(every one: the listing failed)\tunread\n' "$scope" >>"$IAM_EVIDENCE_INDEX"
  fi
}

# A BigQuery REST read (GET, or POST with an empty body for getIamPolicy), the token read from a
# pipe (common.sh, ema_flow_header).
bigquery_evidence_request() {
  local method="$1" url="$2" token
  token="$(ema_flow_access_token)" || return 1
  if [[ "$method" == "POST" ]]; then
    curl --fail --silent --show-error --request POST --data '{}' \
      --header 'Content-Type: application/json' \
      --header @<(ema_flow_header Authorization "Bearer ${token}") "$url"
  else
    curl --fail --silent --show-error --header @<(ema_flow_header Authorization "Bearer ${token}") "$url"
  fi
}

export_effective_iam() {
  echo "=== effective IAM export ==="
  local out_dir stamp date_path bucket dataset store ancestors id kind name ring key deployer
  local bq="https://bigquery.googleapis.com/bigquery/v2/projects/${PROJECT_ID}"
  if ! out_dir="$(mktemp -d)"; then
    echo "::warning::Could not create a temporary directory for the effective IAM export; skipping it."
    return 0
  fi
  mkdir -p "${out_dir}/policies" "${out_dir}/reports"
  IAM_EVIDENCE_INDEX="${out_dir}/policies/index.tsv"
  IAM_EVIDENCE_COUNT=0
  : >"$IAM_EVIDENCE_INDEX"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  date_path="$(date -u +%Y/%m/%d)"
  bucket="$(terraform -chdir=infra output -raw evidence_bucket 2>/dev/null || true)"
  dataset="$(terraform -chdir=infra output -raw healthcare_dataset_id 2>/dev/null || true)"

  # The project, and every folder and the organisation above it.
  iam_evidence_read project "$PROJECT_ID" iam gcloud --quiet projects get-iam-policy "$PROJECT_ID" --format=json
  if ancestors="$(gcloud --quiet projects get-ancestors "$PROJECT_ID" --format='value(id,type)' 2>/dev/null)"; then
    while read -r id kind; do
      case "$kind" in
        folder) iam_evidence_read folder "$id" iam gcloud --quiet resource-manager folders get-iam-policy "$id" --format=json ;;
        organization) iam_evidence_read organization "$id" iam gcloud --quiet organizations get-iam-policy "$id" --format=json ;;
      esac
    done <<<"$ancestors"
  else
    printf -- '-\tfolders and organisation\t(above %s)\tunread\n' "$PROJECT_ID" >>"$IAM_EVIDENCE_INDEX"
  fi

  # The Healthcare dataset and its FHIR stores (the services' FHIR roles are bound on each store).
  if [[ -n "$dataset" ]]; then
    iam_evidence_read healthcare-dataset "$dataset" iam gcloud --quiet healthcare datasets get-iam-policy "$dataset" \
      --location="$REGION" --project="$PROJECT_ID" --format=json
    for store in "$(terraform -chdir=infra output -raw source_fhir_store_id 2>/dev/null || true)" \
      "$(terraform -chdir=infra output -raw target_fhir_store_id 2>/dev/null || true)"; do
      [[ -z "$store" ]] && continue
      iam_evidence_read fhir-store "$store" iam gcloud --quiet healthcare fhir-stores get-iam-policy "$store" \
        --dataset="$dataset" --location="$REGION" --project="$PROJECT_ID" --format=json
    done
  else
    printf -- '-\thealthcare-dataset\t(terraform output healthcare_dataset_id was empty)\tunread\n' >>"$IAM_EVIDENCE_INDEX"
  fi

  # Every bucket, key, topic, image repository, Cloud Run service and service account in the
  # project: listed, not taken from Terraform, so a resource made outside it is read too.
  while read -r name; do
    if [[ -n "$name" ]]; then
      iam_evidence_read bucket "$name" iam gcloud --quiet storage buckets get-iam-policy "gs://${name}" --format=json
    fi
  done < <(iam_evidence_list bucket gcloud --quiet storage buckets list --project="$PROJECT_ID" --format='value(name)')
  while read -r ring; do
    [[ -z "$ring" ]] && continue
    while read -r key; do
      if [[ -n "$key" ]]; then
        iam_evidence_read kms-key "${key##*/}" iam gcloud --quiet kms keys get-iam-policy "$key" --format=json
      fi
    done < <(iam_evidence_list kms-key gcloud --quiet kms keys list --keyring="$ring" --format='value(name)')
  done < <(iam_evidence_list kms-key-ring gcloud --quiet kms keyrings list --location="$REGION" --project="$PROJECT_ID" --format='value(name)')
  while read -r name; do
    if [[ -n "$name" ]]; then
      iam_evidence_read pubsub-topic "${name##*/}" iam gcloud --quiet pubsub topics get-iam-policy "$name" --format=json
    fi
  done < <(iam_evidence_list pubsub-topic gcloud --quiet pubsub topics list --project="$PROJECT_ID" --format='value(name)')
  while read -r name; do
    if [[ -n "$name" ]]; then
      iam_evidence_read artifact-repository "${name##*/}" iam gcloud --quiet artifacts repositories get-iam-policy "${name##*/}" \
        --location="$REGION" --project="$PROJECT_ID" --format=json
    fi
  done < <(iam_evidence_list artifact-repository gcloud --quiet artifacts repositories list --location="$REGION" --project="$PROJECT_ID" --format='value(name)')
  while read -r name; do
    if [[ -n "$name" ]]; then
      iam_evidence_read cloud-run-service "$name" iam gcloud --quiet run services get-iam-policy "$name" \
        --region="$REGION" --project="$PROJECT_ID" --format=json
    fi
  done < <(iam_evidence_list cloud-run-service gcloud --quiet run services list --region="$REGION" --project="$PROJECT_ID" --format='value(metadata.name)')
  while read -r name; do
    if [[ -n "$name" ]]; then
      iam_evidence_read service-account "$name" iam gcloud --quiet iam service-accounts get-iam-policy "$name" \
        --project="$PROJECT_ID" --format=json
    fi
  done < <(iam_evidence_list service-account gcloud --quiet iam service-accounts list --project="$PROJECT_ID" --format='value(email)')

  # Every BigQuery dataset's access list, and the IAM policy of each of its tables.
  while read -r name; do
    [[ -z "$name" ]] && continue
    iam_evidence_read bigquery-dataset "$name" bigquery-dataset bigquery_evidence_request GET "${bq}/datasets/${name}"
    while read -r table; do
      if [[ -n "$table" ]]; then
        iam_evidence_read bigquery-table "${name}.${table}" iam \
          bigquery_evidence_request POST "${bq}/datasets/${name}/tables/${table}:getIamPolicy"
      fi
    done < <(iam_evidence_list bigquery-table bigquery_evidence_request GET "${bq}/datasets/${name}/tables?maxResults=1000" |
      python3 -c "import sys,json;[print(t['tableReference']['tableId']) for t in json.load(sys.stdin).get('tables') or []]" 2>/dev/null || true)
  done < <(iam_evidence_list bigquery-dataset bigquery_evidence_request GET "${bq}/datasets?all=true&maxResults=1000" |
    python3 -c "import sys,json;[print(d['datasetReference']['datasetId']) for d in json.load(sys.stdin).get('datasets') or []]" 2>/dev/null || true)

  # Who each identity is: the three service accounts infra/ declares, and the deployer this runs as.
  deployer="$(gcloud --quiet auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1 || true)"
  local identities=(
    "worker=ema-flow-worker-${ENVIRONMENT}@${PROJECT_ID}.iam.gserviceaccount.com"
    "query=ema-flow-query-${ENVIRONMENT}@${PROJECT_ID}.iam.gserviceaccount.com"
    "caller=ema-flow-caller-${ENVIRONMENT}@${PROJECT_ID}.iam.gserviceaccount.com"
  )
  if [[ "$deployer" == *.gserviceaccount.com ]]; then
    identities+=("deployer=${deployer}!")
  else
    echo "::warning title=IAM evidence incomplete::The deploy is not running as a service account, so the deployer's grants are not exported."
  fi
  if terraform -chdir=infra show -json >"${out_dir}/policies/state.json" 2>/dev/null &&
    python3 scripts/ci/effective-iam.py "$IAM_EVIDENCE_INDEX" "${out_dir}/policies/state.json" \
      "${out_dir}/reports" "${identities[@]}"; then
    if [[ -z "$bucket" ]]; then
      echo "::warning::terraform output evidence_bucket was empty; the effective IAM export is in this deploy log only."
    else
      # Dated path, then the timestamp, environment and the deployed version, so one export
      # belongs to exactly one apply.
      # The full SERVICE_VERSION, -dirty-<tree> included, so a report is never filed under a
      # commit its code was not.
      local destination="gs://${bucket}/deploy-evidence/${date_path}/${stamp}-${ENVIRONMENT}-${SERVICE_VERSION}/"
      if gcloud --quiet storage cp "${out_dir}/reports/"*.json "$destination" >/dev/null 2>&1; then
        echo "Effective IAM export written to ${destination}"
      else
        echo "::warning::Could not upload the effective IAM export to ${destination}; its summary is in this deploy log only."
      fi
    fi
  else
    echo "::warning title=IAM evidence::The effective IAM report could not be built (the Terraform state or scripts/ci/effective-iam.py failed); no export was written."
  fi
  rm -rf "$out_dir"
  return 0
}

# Permissions the apply needs that the deployer's hand-made roles (README.md) did not always carry,
# one line per kind of resource infra/ declares that needs one (test/infra/deploy-preflight.test.ts
# keeps the two in step). An apply that lacks one does not stop cleanly: Terraform refuses the
# create that needs it while carrying out every change that does not, so a grant can be removed
# before its replacement exists. So they are tested first, and a missing one stops the deploy
# before anything changes.
APPLY_PERMISSIONS=(
  iam.roles.create # google_project_iam_custom_role
  iam.roles.delete
  iam.roles.get
  iam.roles.update
  bigquery.tables.getIamPolicy # google_bigquery_table_iam_member
  bigquery.tables.setIamPolicy
  healthcare.fhirStores.getIamPolicy # google_healthcare_fhir_store_iam_member
  healthcare.fhirStores.setIamPolicy
  resourcemanager.projects.getIamPolicy # google_project_iam_audit_config, google_project_iam_member
  resourcemanager.projects.setIamPolicy
)

# The narrowest predefined role that carries each permission above, for the message that says
# how to grant a missing one.
apply_permission_role() {
  case "$1" in
    iam.roles.*) echo "roles/iam.roleAdmin" ;;
    bigquery.tables.*) echo "roles/bigquery.dataOwner" ;;
    healthcare.fhirStores.*) echo "roles/healthcare.fhirStoreAdmin" ;;
    resourcemanager.projects.*) echo "roles/resourcemanager.projectIamAdmin" ;;
    *) echo "a role carrying $1" ;;
  esac
}

# Asks Resource Manager which of APPLY_PERMISSIONS the deploy's own credential holds on the
# project (testIamPermissions needs no permission of its own) and fails, naming the missing ones
# and the gcloud command that grants each role that carries them, unless it holds all of them.
# The question changes nothing, so no answer, a 429 or a 5xx is asked again, three times in all;
# any other refusal, or an answer it cannot read, fails.
preflight_apply_permissions() {
  local body response status attempt missing permission account role token
  body="$(python3 -c 'import json,sys;print(json.dumps({"permissions":sys.argv[1:]}))' "${APPLY_PERMISSIONS[@]}")"
  response="$(mktemp)"
  token="$(ema_flow_access_token)"
  for attempt in 1 2 3; do
    status="$(curl --silent --output "$response" --write-out '%{http_code}' --request POST \
      --header @<(ema_flow_header Authorization "Bearer ${token}") \
      --header 'Content-Type: application/json' \
      --data "$body" \
      "https://cloudresourcemanager.googleapis.com/v1/projects/${PROJECT_ID}:testIamPermissions" || true)"
    case "$status" in
      000 | 429 | 5??)
        if [[ "$attempt" -lt 3 ]]; then
          echo "testIamPermissions answered ${status:-nothing} (attempt ${attempt}/3); asking again in ${PREFLIGHT_RETRY_SECONDS:-5}s."
          sleep "${PREFLIGHT_RETRY_SECONDS:-5}"
          continue
        fi
        ;;
    esac
    break
  done
  if [[ "$status" != "200" ]]; then
    rm -f "$response"
    echo "::error title=Deploy permissions::Could not ask Resource Manager which permissions the deploy holds (testIamPermissions answered HTTP ${status}); not applying." >&2
    return 1
  fi
  if ! missing="$(python3 -c '
import json, sys
held = set(json.load(open(sys.argv[1])).get("permissions") or [])
print(" ".join(p for p in sys.argv[2:] if p not in held))
' "$response" "${APPLY_PERMISSIONS[@]}")"; then
    rm -f "$response"
    echo "::error title=Deploy permissions::Resource Manager answered testIamPermissions with something unreadable; not applying." >&2
    return 1
  fi
  rm -f "$response"
  if [[ -n "$missing" ]]; then
    account="$(gcloud --quiet auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1 || true)"
    echo "::error title=Deploy permissions::The deploy identity lacks ${missing}. Nothing was applied. Grant the roles below, then deploy again." >&2
    while read -r role; do
      echo "  gcloud projects add-iam-policy-binding ${PROJECT_ID} --member=serviceAccount:${account:-<deployer service account>} --role=${role} --condition=None" >&2
    done < <(for permission in $missing; do apply_permission_role "$permission"; done | sort -u)
    return 1
  fi
  echo "deploy permissions: all ${#APPLY_PERMISSIONS[@]} the apply needs are held"
}

# The services' FHIR grants are bound on each store (infra/security.tf, infra/query.tf), and the
# stores are created by reconcile-fhir-stores.sh, not by Terraform. So before an apply, a missing
# store is created first; an existing one is left for phase_bootstrap to reconcile as before.
# Only the HTTP status of each lookup is read. A project with no dataset in the state yet (the
# first deploy) has nowhere to create a store: its apply creates the dataset and fails at the
# store grants, and the next deploy creates the stores here and completes.
ensure_fhir_stores() {
  local dataset store status token missing=0
  dataset="$(terraform -chdir=infra output -raw healthcare_dataset_id 2>/dev/null || true)"
  if [[ -z "$dataset" ]]; then
    echo "::notice::No Healthcare dataset in the Terraform state yet. This apply creates it and then fails at the FHIR store grants, because the stores do not exist; deploy again and the stores are created before the next apply."
    return 0
  fi
  token="$(ema_flow_access_token)"
  for store in "$(terraform -chdir=infra output -raw source_fhir_store_id)" \
    "$(terraform -chdir=infra output -raw target_fhir_store_id)"; do
    if [[ -z "$store" ]]; then
      echo "::error::terraform output named no FHIR store id; cannot check the stores exist." >&2
      return 1
    fi
    status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
      --header @<(ema_flow_header Authorization "Bearer ${token}") \
      "https://healthcare.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/datasets/${dataset}/fhirStores/${store}" || true)"
    case "$status" in
      200) ;;
      404) missing=1 ;;
      *)
        echo "::error::Could not look up FHIR store ${store}: HTTP ${status}." >&2
        return 1
        ;;
    esac
  done
  if [[ "$missing" == "1" ]]; then
    echo "A FHIR store is missing; creating it before the apply grants access to it."
    GOOGLE_CLOUD_PROJECT="$PROJECT_ID" bash scripts/gcp/reconcile-fhir-stores.sh
  fi
}

# The operations dashboard's text is ignored by Terraform (infra/observability.tf), so this is
# what keeps it current: when the live dashboard lacks a configured value, it is replaced. A
# dashboard holds no record, so replacing it loses nothing. Anything this cannot read or compare
# fails the deploy, like a failed apply: an unchecked dashboard is how the perpetual diff hid.
sync_dashboard() {
  echo "=== operations dashboard ==="
  local id configured live verdict=0
  id="$(terraform -chdir=infra output -raw operations_dashboard_id 2>/dev/null || true)"
  configured="$(mktemp)"
  live="$(mktemp)"
  if [[ -z "$id" ]] ||
    ! terraform -chdir=infra output -raw operations_dashboard_json >"$configured" 2>/dev/null ||
    ! gcloud --quiet monitoring dashboards describe "$id" --format=json >"$live"; then
    echo "::error title=Dashboard drift::Could not read the operations dashboard or its configuration." >&2
    rm -f "$configured" "$live"
    return 1
  fi
  python3 scripts/ci/dashboard-drift.py "$configured" "$live" || verdict=$?
  if [[ "$verdict" == "1" ]]; then
    echo "Replacing the operations dashboard with its configuration."
    terraform -chdir=infra apply \
      -input=false \
      -auto-approve \
      -replace=google_monitoring_dashboard.operations \
      -target=google_monitoring_dashboard.operations \
      "${TF_DEPLOY_VARS[@]}"

    id="$(terraform -chdir=infra output -raw operations_dashboard_id)"
    if ! gcloud --quiet monitoring dashboards describe "$id" --format=json >"$live"; then
      echo "::error title=Dashboard drift::Could not read the replaced operations dashboard." >&2
      rm -f "$configured" "$live"
      return 1
    fi
    verdict=0
    python3 scripts/ci/dashboard-drift.py "$configured" "$live" || verdict=$?
    if [[ "$verdict" == "1" ]]; then
      echo "::warning::The replaced dashboard still differs from its configuration: scripts/ci/dashboard-drift.py does not model some rewrite the Monitoring API makes. Every deploy will replace it until that is fixed."
      verdict=0
    fi
  fi
  rm -f "$configured" "$live"
  # 2 (or anything but 0 and 1) is the checker failing, never drift.
  if [[ "$verdict" != "0" ]]; then
    echo "::error title=Dashboard drift::scripts/ci/dashboard-drift.py could not compare the dashboard with its configuration (exit ${verdict})." >&2
    return 1
  fi
}

phase_apply() {
  echo "=== terraform apply ==="
  require_provenance
  echo "service_version=${SERVICE_VERSION}"
  WORKER_DIGEST="$(resolve_image_digest worker "$TAG")"
  VALIDATOR_DIGEST="$(resolve_image_digest validator "$TAG")"
  QUERY_DIGEST="$(resolve_image_digest query "$TAG")"

  # The account this deploy runs as, granted roles/run.invoker on the worker
  # (google_cloud_run_v2_service_iam_member.deployer_invoker in infra/run.tf) so phase_smoke can
  # call the service this apply just deployed. Only a service account is passed: a human's
  # account cannot mint an ID token for the worker's audience at all, so binding one would leave
  # a standing privilege on the worker that no documented path can exercise. A local operator
  # supplies WORKER_ID_TOKEN instead (phase_smoke says how), which authenticates as the
  # impersonated service account and needs no binding for the human.
  local deployer_account_input
  deployer_account_input="$(gcloud --quiet auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1 || true)"
  if [[ -z "$deployer_account_input" ]]; then
    echo "::warning::Could not determine the active gcloud account; the deployer's run.invoker binding on the worker is not declared and the smoke run will be refused."
  elif [[ "$deployer_account_input" != *.gserviceaccount.com ]]; then
    echo "::notice::Deploying as a user account, so no deployer run.invoker binding is declared; phase_smoke needs WORKER_ID_TOKEN."
    deployer_account_input=""
  fi

  tf_deploy_vars "$deployer_account_input" \
    "${REPOSITORY}/worker@${WORKER_DIGEST}" \
    "${REPOSITORY}/validator@${VALIDATOR_DIGEST}" \
    "${REPOSITORY}/query@${QUERY_DIGEST}" \
    "$SERVICE_VERSION"

  preflight_apply_permissions
  ensure_fhir_stores

  plan_reviewed apply "${TF_DEPLOY_VARS[@]}"
  if ! terraform -chdir=infra apply -input=false "$REVIEWED_PLAN"; then
    rm -f "$REVIEWED_PLAN"
    echo "=== terraform apply failed; dumping recent container logs for diagnosis ===" >&2
    # Only the worker's and the query service's structured logs, whose fields are sanitised by
    # src/lib/logger.ts, and only the five fields named in --format; the validator sidecar's
    # free-text console output is never copied into deploy logs.
    local service
    for service in worker query; do
      echo "--- ema-flow-${ENVIRONMENT}-${service} ---" >&2
      gcloud --quiet logging read \
        "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"ema-flow-${ENVIRONMENT}-${service}\" AND resource.labels.container_name=\"${service}\"" \
        --project="$PROJECT_ID" \
        --order=asc \
        --freshness=1h \
        --limit=500 \
        --format="value(timestamp,severity,jsonPayload.stage,jsonPayload.message,jsonPayload.errorCount)" || true
    done
    return 1
  fi
  rm -f "$REVIEWED_PLAN"

  # The endpoint and the audience are different hostnames; printing both here keeps a caller
  # from minting a token for the wrong one (infra/outputs.tf). The third is the account the
  # ID-token recipe in README.md impersonates.
  terraform -chdir=infra output query_service_url || true
  terraform -chdir=infra output query_audience || true
  terraform -chdir=infra output query_caller_service_account || true

  sync_dashboard
  export_effective_iam
}

# A pull request's plan against live state (.github/workflows/plan.yml; foundations B4), run as
# the read-only planner. Every input is what is deployed now — the running images, the running
# service version, the deployer the deploy runs as — so the plan shows only what the pull request
# itself changes, not the image churn every deploy carries. Writes the plan text to PLAN_OUT
# (default plan.txt) and a redacted summary to PLAN_SUMMARY (default plan-summary.md).
# Exits 1 on a plan error, and 3 when the plan destroys or replaces anything and ALLOW_REPLACE is
# not "true": a destroy on merge is applied unattended, so it must be acknowledged on the pull
# request (label `allow-replace`) before the check passes.
phase_plan() {
  echo "=== terraform plan (read-only) ==="
  local out="${PLAN_OUT:-plan.txt}" summary="${PLAN_SUMMARY:-plan-summary.md}"
  local worker_service="ema-flow-${ENVIRONMENT}-worker" query_service="ema-flow-${ENVIRONMENT}-query"
  local images live_version
  images="$(gcloud --quiet run services describe "$worker_service" --region="$REGION" --format=json |
    python3 -c "import sys,json;c={x.get('name','x'):x['image'] for x in json.load(sys.stdin)['spec']['template']['spec']['containers']};print(c['worker'],c['validator'])")"
  live_version="$(gcloud --quiet run services describe "$query_service" --region="$REGION" --format=json |
    python3 -c "import sys,json;c=json.load(sys.stdin)['spec']['template']['spec']['containers'][0];print(next(e['value'] for e in c.get('env',[]) if e['name']=='QUERY_SERVICE_VERSION'))")"
  local query_image
  query_image="$(gcloud --quiet run services describe "$query_service" --region="$REGION" --format='value(spec.template.spec.containers[0].image)')"

  tf_deploy_vars "${DEPLOY_SERVICE_ACCOUNT:?DEPLOY_SERVICE_ACCOUNT names the account the deploy runs as}" \
    "${images% *}" "${images#* }" "$query_image" "$live_version"

  local code=0 plan_file plan_json="-"
  plan_file="$(mktemp)"
  terraform -chdir=infra plan -input=false -lock=false -no-color -detailed-exitcode \
    -out="$plan_file" "${TF_DEPLOY_VARS[@]}" >"$out" 2>&1 || code=$?
  if [[ "$code" != "1" ]]; then
    plan_json="${out%.*}.json"
    terraform -chdir=infra show -json "$plan_file" >"$plan_json"
  fi
  rm -f "$plan_file"
  # The verdict fails closed: only 0 (no destroy) and 4 (destroy) are verdicts; anything else,
  # including a crash of the summariser, fails the check.
  local verdict=0
  python3 scripts/ci/plan-summary.py "$plan_json" "$out" "$summary" "$code" || verdict=$?
  if [[ "$plan_json" != "-" && -f "$summary" ]]; then
    plan_dashboard_drift "$plan_json" >>"$summary"
  fi
  [[ "$plan_json" != "-" ]] && rm -f "$plan_json"
  case "$verdict" in
    0) return 0 ;;
    4)
      if [[ "${ALLOW_REPLACE:-false}" == "true" ]]; then return 0; fi
      echo "::error::The plan destroys or replaces resources. Label the pull request allow-replace once reviewed." >&2
      return 3
      ;;
    *) return 1 ;;
  esac
}

# One summary line on the operations dashboard, whose text Terraform ignores (sync_dashboard):
# whether the pull request's configuration of it differs from the live dashboard, which the
# deploy would then replace. Informational; it never changes the plan's verdict.
plan_dashboard_drift() {
  local plan_json="$1" id configured live verdict=0
  configured="$(mktemp)"
  live="$(mktemp)"
  id="$(terraform -chdir=infra output -raw operations_dashboard_id 2>/dev/null || true)"
  if [[ -n "$id" ]] &&
    python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['planned_values']['outputs']['operations_dashboard_json']['value'])" "$plan_json" >"$configured" 2>/dev/null &&
    gcloud --quiet monitoring dashboards describe "$id" --format=json >"$live" 2>/dev/null; then
    python3 scripts/ci/dashboard-drift.py "$configured" "$live" >/dev/null || verdict=$?
  else
    verdict=2
  fi
  rm -f "$configured" "$live"
  echo ""
  case "$verdict" in
    0) echo "The operations dashboard matches its configuration." ;;
    1) echo "**The operations dashboard differs from its configuration; the deploy will replace it.**" ;;
    *) echo "The operations dashboard's drift could not be checked (its text is ignored by Terraform)." ;;
  esac
}

# bootstrap.sh reads the standards and the synthetic fixture from DEPLOY_INPUTS_DIR (made by
# scripts/gcp/deploy-inputs.sh, in the workflow's gate job or by phase_inputs), and seeds the
# fixture only where the environment's inputs accept synthetic content.
phase_bootstrap() {
  echo "=== reconcile FHIR stores and import profiles ==="
  GOOGLE_CLOUD_PROJECT="$PROJECT_ID" bash scripts/gcp/reconcile-fhir-stores.sh
  # DEPLOY_COMMIT is what ALLOW_REPLACE_ACK must name for bootstrap to prune more than its bound
  # allows (bootstrap.sh, check_prune_bound).
  deploy_provenance
  GOOGLE_CLOUD_PROJECT="$PROJECT_ID" ALLOW_SYNTHETIC_SOURCES="${ALLOW_SYNTHETIC_SOURCES:-false}" \
    DEPLOY_COMMIT="$SERVICE_VERSION" bash scripts/gcp/bootstrap.sh
  echo "Deployment complete."
  terraform -chdir=infra output workflow_console_url
  terraform -chdir=infra output bigquery_console_url
}

# One fixture run through the worker this deploy just applied, which must answer HTTP 200 with
# status "persisted". Until this step existed a deploy was green whether or not the pipeline
# could complete a run, and in this project it never had: every source failed official
# validation inside the worker, and nothing outside the worker ever asked. Runs after
# phase_bootstrap rather than straight after phase_apply because a persisted run needs the
# R5 stores reconciled and the profiles imported first.
#
# The ID token comes from WORKER_ID_TOKEN when the caller supplies one, because the credential
# a deploy actually runs under cannot mint it. Under Workload Identity Federation gcloud holds
# an *external account* credential, and `print-identity-token --audiences=` refuses those for
# the same reason it refuses a human's account: neither has an ID token to hand out. The
# workflow therefore mints it with google-github-actions/auth (token_format: id_token), which
# reaches the deployer service account's generateIdToken through the
# roles/iam.workloadIdentityUser binding the GitHub pool principal already holds, and passes it
# in. The gcloud fallback below is kept for the credential kinds that do support the flag — a
# key file, an impersonation-configured gcloud, or a GCE service account — so a local run still
# works without the workflow.
#
# For Cloud Run to accept the token, the identity it authenticates as must be able to invoke
# the worker. The deployer can today through its project-level roles/run.admin, which contains
# run.routes.invoke; infra/run.tf additionally declares an explicit run.invoker binding
# (google_cloud_run_v2_service_iam_member.deployer_invoker) from the account phase_apply passes
# as deployer_account. A 403 here is therefore only expected if that project role is ever
# narrowed and the explicit binding is still propagating, which is what the retry below covers.
#
# A production environment may set enabled_run_sources without "fixture" (ADR 0002): the worker
# then answers 422 source-disabled before touching anything, and this step skips with a notice
# instead of failing, because that answer is the allowlist working as configured.
#
# Only closed fields of the answer are printed (status, error, reason, runId, hashes, counts);
# the deploy log is attached to a GitHub issue on failure and must carry no payload.
phase_smoke() {
  echo "=== smoke: one fixture run through the deployed worker ==="
  local worker_url token body_file http_code attempt verdict
  worker_url="$(terraform -chdir=infra output -raw cloud_run_service_uri 2>/dev/null || true)"
  if [[ -z "$worker_url" ]]; then
    echo "::error title=Smoke run::terraform output cloud_run_service_uri was empty; nothing to call." >&2
    return 1
  fi
  echo "worker=${worker_url}"
  # Which source produced the token is printed; the token itself never is.
  token="${WORKER_ID_TOKEN:-}"
  if [[ -n "$token" ]]; then
    echo "token source: WORKER_ID_TOKEN supplied by the caller"
  elif token="$(gcloud --quiet auth print-identity-token --audiences="$worker_url" 2>/dev/null)" &&
    [[ -n "$token" ]]; then
    echo "token source: gcloud print-identity-token as the active account"
  else
    # The impersonation recipe is not restated here: it needs a one-time
    # roles/iam.serviceAccountTokenCreator grant that project owner does not carry, and a message
    # that gave the command without the grant would send an operator into a PERMISSION_DENIED
    # this repository has already recorded. README.md carries both, together.
    echo "::error title=Smoke run::Could not obtain an ID token for ${worker_url}. gcloud refuses --audiences for external-account (Workload Identity Federation) and user credentials alike, so supply one in WORKER_ID_TOKEN. The recipe, including the one-time roles/iam.serviceAccountTokenCreator grant it needs first, is in README.md under 'Re-ingesting with scripts/demo/seed.ts'." >&2
    return 1
  fi

  body_file="$(mktemp)"
  # Only 403 is retried, and only because an apply seconds earlier may still be propagating an
  # IAM binding. The other two codes a first draft retried are wrong to retry:
  #
  #   401 means the token was refused, not the caller. Sleeping does not mint a new one, so the
  #   attempts only delay the failure while blaming an IAM binding that was never involved.
  #   403 is what a missing or propagating binding actually answers.
  #
  #   000 is curl reporting no HTTP answer at all, and it cannot distinguish a connection that
  #   never opened from a request the worker received and is still executing. A run is not
  #   idempotent — it persists a document and writes evidence and a ledger row — so re-POSTing
  #   after a timeout risks a second run of the first one. Failing honestly is the lesser harm.
  #
  # --max-time is 480s, comfortably inside the ID token's 10-minute maximum lifetime, so no
  # attempt can outlive the credential it is carrying. With only 403 retried the answers are
  # immediate, so the whole loop is bounded by the propagation budget (105s) rather than by
  # eight timeouts, and cannot approach the job's timeout-minutes.
  local max_attempts=8
  for attempt in $(seq 1 "$max_attempts"); do
    http_code="$(curl --silent --show-error --output "$body_file" --write-out '%{http_code}' \
      --max-time 480 \
      --request POST "${worker_url}/v1/runs" \
      --header @<(ema_flow_header Authorization "Bearer ${token}") \
      --header 'Content-Type: application/json' \
      --data '{"source":"fixture"}' || true)"
    if [[ "$http_code" == "403" && "$attempt" -lt "$max_attempts" ]]; then
      echo "worker answered HTTP 403 (attempt ${attempt}/${max_attempts}); retrying in 15s in case the run.invoker binding is still propagating."
      sleep 15
      continue
    fi
    case "$http_code" in
      401)
        echo "worker answered HTTP 401: the ID token was refused. Not retried — a retry presents the same token. Check the audience matches ${worker_url} and that the token carries an e-mail claim." >&2
        ;;
      000)
        echo "worker returned no HTTP answer within 480s. Not retried, because a run that may already be executing is not safe to repeat." >&2
        ;;
    esac
    break
  done

  # 0: persisted; 3: source disabled, skip; 1: anything else. Non-JSON bodies (Cloud Run's own
  # 401/403/404 pages) yield no fields, and no body text is ever printed.
  verdict=0
  python3 - "$http_code" "$body_file" <<'PY' || verdict=$?
import json
import sys

code, path = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as handle:
        body = json.load(handle)
except Exception:
    body = None
if not isinstance(body, dict):
    body = {}
fields = {
    key: body[key]
    for key in ("status", "error", "reason", "runId", "manifestHash", "targetBundleId", "mappingDecisions")
    if key in body
}
validation = body.get("validation")
if isinstance(validation, dict):
    fields["validation"] = {key: value for key, value in validation.items() if key != "profiles"}
print(f"HTTP {code}: {json.dumps(fields, sort_keys=True)}")
if code == "422" and fields.get("error") == "source-disabled":
    print("::notice title=Smoke run skipped::the worker's run-source allowlist (enabled_run_sources) excludes fixture, so no fixture run was attempted; this is the allowlist working as configured.")
    sys.exit(3)
if code == "200" and fields.get("status") == "persisted":
    print(f"Smoke run persisted: runId={fields.get('runId')} targetBundleId={fields.get('targetBundleId')}")
    sys.exit(0)
if code == "200":
    print(f"::error title=Smoke run failed::the worker answered 200 with status {fields.get('status')!r}, not \"persisted\" (is DRY_RUN set on the service?).")
    sys.exit(1)
print(f"::error title=Smoke run failed::HTTP {code}, error={fields.get('error')!r}, reason={fields.get('reason')!r}. A reason of official-validation-failed or cloud-validation-failed means the fixture does not conform; run `npm run validate:official` locally.")
sys.exit(1)
PY
  rm -f "$body_file"
  case "$verdict" in
    0) return 0 ;;
    3) return 0 ;;
    *) return 1 ;;
  esac
}

# The query service after a deploy: up, this commit's revision, and walled at both layers. No
# tool is called; the authenticated get_section / verify_quote smoke is still to come.
#
#   1. Cloud Run reports the service Ready and serving the revision it created last: the
#      revision this deploy made passed its startup probe (/healthz, inside the container).
#   2. Anonymous, with no credential at all, POST /mcp and GET /readyz are both refused, 401 or
#      403. The service has no allUsers invoker (infra/query.tf), so this is Cloud Run's IAM edge
#      answering: nothing about the service is public, and a missing service would be 404.
#   3. With QUERY_EDGE_ID_TOKEN — an ID token for the service URL, of an identity that may invoke
#      it, carried in X-Serverless-Authorization the way Gemini Enterprise carries its own — the
#      request passes the edge and reaches the container, with no Authorization header:
#        POST /mcp must be the container's own `401 {"error":"unauthenticated"}`: the service is
#        up, and its Bearer check refuses a caller that passed Cloud Run's;
#        GET /readyz must be 200 with status ok, service ema-flow-query, and version equal to
#        this deploy's SERVICE_VERSION: the revision answering is the one just deployed.
#      Without the token, step 3 is skipped with a notice (a local run as a user account).
#
# Only status codes and closed fields of a JSON body (error, status, service, version) are
# printed. A request that fails these checks never reaches a tool, so no answer (000) and a
# frontend 502/503/504 — a cold start — are retried; nothing else is.
query_smoke_request() {
  local method="$1" path="$2" edge_token="${3:-}" body_file attempt
  body_file="$(mktemp)"
  for attempt in 1 2 3; do
    local args=(--silent --output "$body_file" --write-out '%{http_code}' --max-time 30
      --request "$method")
    if [[ "$method" == "POST" ]]; then
      args+=(--header 'Content-Type: application/json'
        --header 'Accept: application/json, text/event-stream'
        --data '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
    fi
    # The token is read from a pipe on the command itself, never put in curl's arguments
    # (common.sh, ema_flow_header).
    if [[ -n "$edge_token" ]]; then
      QS_CODE="$(curl "${args[@]}" \
        --header @<(ema_flow_header X-Serverless-Authorization "Bearer ${edge_token}") \
        "${QUERY_SMOKE_URL}${path}" || true)"
    else
      QS_CODE="$(curl "${args[@]}" "${QUERY_SMOKE_URL}${path}" || true)"
    fi
    case "$QS_CODE" in
      000 | 502 | 503 | 504)
        if [[ "$attempt" -lt 3 ]]; then
          echo "${method} ${path} answered ${QS_CODE} (attempt ${attempt}/3); retrying in ${QUERY_SMOKE_RETRY_SECONDS:-10}s." >&2
          sleep "${QUERY_SMOKE_RETRY_SECONDS:-10}"
          continue
        fi
        ;;
    esac
    break
  done
  QS_FIELDS="$(python3 - "$body_file" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        body = json.load(handle)
except Exception:
    body = None
if not isinstance(body, dict):
    body = {}


def closed(key):
    value = body.get(key)
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", value):
        return value
    return "-"


print(" ".join(f"{key}={closed(key)}" for key in ("error", "status", "service", "version")))
PY
)"
  rm -f "$body_file"
}

# Fails unless the last request was refused at the edge (401 or 403), saying which way it was not.
query_smoke_expect_refused() {
  local label="$1"
  case "$QS_CODE" in
    401 | 403) return 0 ;;
    2??)
      echo "::error title=Query smoke failed::${label} answered ${QS_CODE} to a caller with no credential. The query service must refuse every unauthenticated request." >&2
      ;;
    404)
      echo "::error title=Query smoke failed::${label} answered 404: no service answers at ${QUERY_SMOKE_URL}." >&2
      ;;
    *)
      echo "::error title=Query smoke failed::${label} answered ${QS_CODE}, not the 401 or 403 refusal expected of an unauthenticated request." >&2
      ;;
  esac
  return 1
}

phase_query_smoke() {
  echo "=== query-smoke: the deployed query service is up, current, and walled ==="
  deploy_provenance
  local query_service="ema-flow-${ENVIRONMENT}-query" describe_file failed=0 expected
  QUERY_SMOKE_URL="$(terraform -chdir=infra output -raw query_service_url 2>/dev/null || true)"
  if [[ -z "$QUERY_SMOKE_URL" ]]; then
    echo "::error title=Query smoke::terraform output query_service_url was empty; nothing to call." >&2
    return 1
  fi
  echo "query=${QUERY_SMOKE_URL}"

  describe_file="$(mktemp)"
  if ! gcloud --quiet run services describe "$query_service" --region="$REGION" --format=json >"$describe_file"; then
    rm -f "$describe_file"
    echo "::error title=Query smoke::could not describe Cloud Run service ${query_service}." >&2
    return 1
  fi
  if ! python3 - "$describe_file" <<'PY'; then
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    status = (json.load(handle) or {}).get("status") or {}
ready = [c for c in status.get("conditions") or [] if c.get("type") == "Ready"]
created = status.get("latestCreatedRevisionName")
serving = status.get("latestReadyRevisionName")
if not ready or ready[0].get("status") != "True":
    print("::error title=Query smoke failed::Cloud Run does not report the query service Ready.")
    sys.exit(1)
if not created or created != serving:
    print(f"::error title=Query smoke failed::the latest created revision {created!r} is not the latest ready one {serving!r}: this deploy's revision did not start.")
    sys.exit(1)
print(f"query service Ready, serving {serving}")
PY
    rm -f "$describe_file"
    return 1
  fi
  rm -f "$describe_file"

  query_smoke_request POST /mcp
  echo "anonymous POST /mcp: HTTP ${QS_CODE} ${QS_FIELDS}"
  query_smoke_expect_refused "anonymous POST /mcp" || failed=1
  query_smoke_request GET /readyz
  echo "anonymous GET /readyz: HTTP ${QS_CODE} ${QS_FIELDS}"
  query_smoke_expect_refused "anonymous GET /readyz" || failed=1

  # The token itself is never printed; only whether there was one.
  if [[ -z "${QUERY_EDGE_ID_TOKEN:-}" ]]; then
    echo "::notice title=Query smoke::QUERY_EDGE_ID_TOKEN is not set, so the container's own checks (its 401 on /mcp, its /readyz version) were skipped."
    return "$failed"
  fi

  query_smoke_request POST /mcp "$QUERY_EDGE_ID_TOKEN"
  echo "POST /mcp past the edge, no Bearer: HTTP ${QS_CODE} ${QS_FIELDS}"
  if [[ "$QS_CODE" != "401" || "$QS_FIELDS" != "error=unauthenticated "* ]]; then
    echo "::error title=Query smoke failed::POST /mcp with no Bearer, past Cloud Run's edge, answered HTTP ${QS_CODE} (${QS_FIELDS}), not the service's own 401 unauthenticated. A non-JSON 401 or a 403 is the edge refusing QUERY_EDGE_ID_TOKEN; a 2xx is the service admitting a caller it could not identify." >&2
    failed=1
  fi

  query_smoke_request GET /readyz "$QUERY_EDGE_ID_TOKEN"
  echo "GET /readyz past the edge: HTTP ${QS_CODE} ${QS_FIELDS}"
  expected="error=- status=ok service=ema-flow-query version=${SERVICE_VERSION}"
  if [[ "$QS_CODE" != "200" || "$QS_FIELDS" != "$expected" ]]; then
    echo "::error title=Query smoke failed::GET /readyz answered HTTP ${QS_CODE} (${QS_FIELDS}), not 200 from ema-flow-query at version ${SERVICE_VERSION}: the revision answering is not this deploy's." >&2
    failed=1
  fi
  return "$failed"
}

case "$PHASE" in
  preflight) phase_preflight ;;
  deps) phase_deps ;;
  init) phase_init ;;
  apis) phase_apis ;;
  images) phase_images ;;
  apply) phase_apply ;;
  plan) phase_plan ;;
  record-readers) bash scripts/gcp/record-readers.sh ;;
  bootstrap) phase_bootstrap ;;
  smoke) phase_smoke ;;
  query-smoke) phase_query_smoke ;;
  all)
    phase_preflight
    phase_deps
    phase_inputs
    phase_init
    phase_apis
    phase_images
    phase_apply
    bash scripts/gcp/record-readers.sh
    phase_bootstrap
    phase_smoke
    phase_query_smoke
    ;;
  *)
    echo "Unknown deploy phase: ${PHASE}" >&2
    exit 1
    ;;
esac
