#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
cd "$ROOT"

PHASE="${1:-all}"
trap 'echo "::error title=Phase ${PHASE} failed::${BASH_COMMAND} exited $?"' ERR
PROJECT_ID="$(ema_flow_resolve_project)"
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export GCP_PROJECT_ID="${GCP_PROJECT_ID:-$PROJECT_ID}"
export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"
REGION="${GCP_REGION:-europe-west4}"
ENVIRONMENT="${EMA_FLOW_ENVIRONMENT:-dev}"

if [[ -d .git ]]; then
  TAG="$(git rev-parse --short=12 HEAD)"
else
  TAG="${GITHUB_SHA:-manual}"
  TAG="${TAG:0:12}"
fi
# The image repository: named once, here. It is encrypted with the `artifacts` key
# (infra/main.tf, google_artifact_registry_repository.images_cmek; CMEK step 4). The builds, the
# digest lookups and the Cloud Run image references all take it from this variable.
REPOSITORY_ID="ema-flow-images"
REPOSITORY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY_ID}"

# Recorded by the query service in every audit record as QUERY_SERVICE_VERSION, so a record
# can be tied to the commit that produced it. GITHUB_SHA is the full commit in Actions; a
# local run uses HEAD; "local" (the Terraform default) marks a checkout without git.
if [[ -n "${GITHUB_SHA:-}" ]]; then
  SERVICE_VERSION="$GITHUB_SHA"
elif SERVICE_VERSION="$(git rev-parse HEAD 2>/dev/null)" && [[ -n "$SERVICE_VERSION" ]]; then
  :
else
  SERVICE_VERSION="local"
fi

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

  # The entitlement-denial alert's recipient (infra/variables.tf `alert_notification_email`).
  # Until 2026-09-21 nothing passed it, so the variable existed, the metric was created, and no
  # alert could ever fire from a deploy. Presence only is logged: an address is personal data
  # and this log can be attached to a failure issue. Unset, like the query variables above,
  # means the Terraform default -- which also means a deploy run without it removes a channel
  # an earlier deploy created, so set it wherever deploys run.
  local alert_notification_email="${ALERT_NOTIFICATION_EMAIL:-}"
  if [[ -n "$alert_notification_email" ]]; then
    echo "alert configuration: alert_notification_email is set; the denial alert and its e-mail channel are declared"
  else
    echo "alert configuration: alert_notification_email is not set; the denial metric exists with no alert"
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
    # Dev logs why a credential was refused (a category, never the token); production does not.
    -var="query_log_rejection_reason=${QUERY_LOG_REJECTION_REASON:-false}"
  )
}

phase_preflight() {
  echo "=== preflight ==="
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

phase_init() {
  echo "=== terraform init ==="
  local state_bucket="${PROJECT_ID}-ema-flow-tfstate"
  if ! gcloud --quiet storage buckets describe "gs://${state_bucket}" >/dev/null 2>&1; then
    echo "Creating Terraform state bucket gs://${state_bucket}"
    gcloud --quiet storage buckets create "gs://${state_bucket}" \
      --project="$PROJECT_ID" \
      --location="$REGION" \
      --uniform-bucket-level-access
    gcloud --quiet storage buckets update "gs://${state_bucket}" --versioning
  fi
  terraform -chdir=infra init -input=false \
    -backend-config="bucket=${state_bucket}" \
    -backend-config="prefix=terraform/state"
  terraform -chdir=infra fmt -check -recursive
  terraform -chdir=infra validate
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

  # The three placeholder images below carry no digest. The precondition on
  # google_cloud_run_v2_service.query rejects a digest-less query_image, and that resource is
  # not in the -target list, so on this apply the precondition is not evaluated. This phase has
  # not run against a project since the query service was added: that is what the -target list
  # implies, not something observed. If an apply here ever fails on that error message, the
  # -target list is reaching further than it reads.
  terraform -chdir=infra apply \
    -input=false \
    -auto-approve \
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
      --substitutions="_REGION=${REGION},_REPOSITORY=${REPOSITORY_ID},_IMAGE_TAG=${TAG}" \
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
  local digest
  # Artifact Registry's docker v2 endpoint authenticates like `docker login`
  # does: HTTP Basic with the fixed username `oauth2accesstoken` and a GCP
  # access token as the password (not a raw Authorization: Bearer header).
  digest="$(curl --fail --silent --show-error --head \
    --user "oauth2accesstoken:$(ema_flow_access_token)" \
    --header "Accept: application/vnd.docker.distribution.manifest.v2+json,application/vnd.oci.image.manifest.v1+json" \
    "https://${REGION}-docker.pkg.dev/v2/${PROJECT_ID}/${REPOSITORY_ID}/${image_name}/manifests/${tag}" \
    | tr -d '\r' | grep -i '^docker-content-digest:' | awk '{print $2}')"
  if [[ -z "$digest" ]]; then
    echo "Could not resolve a digest for ${image_name}:${tag} from the registry manifest response." >&2
    exit 1
  fi
  printf '%s' "$digest"
}

# Effective IAM of the worker and query service accounts as the project and the Healthcare
# dataset report it after an apply (UR-18 in docs/validation/README.md, ADR 0004 decision 5).
# Terraform state says which bindings this configuration declares; these exports say which
# bindings the platform actually holds, including any added outside Terraform. Every step is
# guarded: a missing permission, a missing output, or a failed upload prints a warning line and
# the function still returns 0, so evidence collection never fails a deploy.
export_effective_iam() {
  echo "=== effective IAM policy export ==="
  local out_dir dataset bucket stamp date_path sa short_name policy_file exported=0
  if ! out_dir="$(mktemp -d)"; then
    echo "::warning::Could not create a temporary directory for the effective IAM export; skipping it."
    return 0
  fi
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  date_path="$(date -u +%Y/%m/%d)"
  dataset="$(terraform -chdir=infra output -raw healthcare_dataset_id 2>/dev/null || true)"
  bucket="$(terraform -chdir=infra output -raw evidence_bucket 2>/dev/null || true)"

  # The third account is the impersonation-only caller (google_service_account.caller). It is
  # expected to hold no project role and no dataset role at all, so its two exports are
  # expected to be empty: that emptiness is the evidence, since its only declared binding is
  # roles/run.invoker on one Cloud Run service, which neither of these policies covers.
  for sa in "ema-flow-worker-${ENVIRONMENT}@${PROJECT_ID}.iam.gserviceaccount.com" \
    "ema-flow-query-${ENVIRONMENT}@${PROJECT_ID}.iam.gserviceaccount.com" \
    "ema-flow-caller-${ENVIRONMENT}@${PROJECT_ID}.iam.gserviceaccount.com"; do
    short_name="${sa%%@*}"

    echo "--- project ${PROJECT_ID}: roles held by ${sa} ---"
    policy_file="${out_dir}/project-iam-${short_name}.json"
    if gcloud --quiet projects get-iam-policy "$PROJECT_ID" \
      --flatten='bindings[].members' \
      --filter="bindings.members:serviceAccount:${sa}" \
      --format='json(bindings.role,bindings.members,bindings.condition)' >"$policy_file"; then
      cat "$policy_file"
      exported=$((exported + 1))
    else
      rm -f "$policy_file"
      echo "::warning::Could not read the project IAM policy for ${sa}. Grant the deployer resourcemanager.projects.getIamPolicy (roles/iam.securityReviewer) to record this evidence."
    fi

    if [[ -z "$dataset" ]]; then
      continue
    fi
    echo "--- healthcare dataset ${dataset}: roles held by ${sa} ---"
    policy_file="${out_dir}/dataset-iam-${short_name}.json"
    if gcloud --quiet healthcare datasets get-iam-policy "$dataset" \
      --location="$REGION" \
      --project="$PROJECT_ID" \
      --flatten='bindings[].members' \
      --filter="bindings.members:serviceAccount:${sa}" \
      --format='json(bindings.role,bindings.members,bindings.condition)' >"$policy_file"; then
      cat "$policy_file"
      exported=$((exported + 1))
    else
      rm -f "$policy_file"
      echo "::warning::Could not read the Healthcare dataset IAM policy for ${sa}. Grant the deployer healthcare.datasets.getIamPolicy to record this evidence."
    fi
  done

  if [[ -z "$dataset" ]]; then
    echo "::warning::terraform output healthcare_dataset_id was empty; the dataset-level export was skipped."
  fi

  if [[ "$exported" -gt 0 && -n "$bucket" ]]; then
    # Dated path, then the timestamp, environment and the first 12 characters of the deployed
    # commit, so one export belongs to exactly one apply.
    local destination="gs://${bucket}/deploy-evidence/${date_path}/${stamp}-${ENVIRONMENT}-${SERVICE_VERSION:0:12}/"
    if gcloud --quiet storage cp "${out_dir}"/*.json "$destination" >/dev/null; then
      echo "Effective IAM export written to ${destination}"
    else
      echo "::warning::Could not upload the effective IAM export to ${destination}; it remains in this deploy log only."
    fi
  elif [[ -z "$bucket" ]]; then
    echo "::warning::terraform output evidence_bucket was empty; the effective IAM export is in this deploy log only."
  fi

  rm -rf "$out_dir"
  return 0
}

phase_apply() {
  echo "=== terraform apply ==="
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

  if ! terraform -chdir=infra apply \
    -input=false \
    -auto-approve \
    "${TF_DEPLOY_VARS[@]}"; then
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

  # The endpoint and the audience are different hostnames; printing both here keeps a caller
  # from minting a token for the wrong one (infra/outputs.tf). The third is the account the
  # ID-token recipe in README.md impersonates.
  terraform -chdir=infra output query_service_url || true
  terraform -chdir=infra output query_audience || true
  terraform -chdir=infra output query_caller_service_account || true

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

phase_bootstrap() {
  echo "=== reconcile FHIR stores and import profiles ==="
  GOOGLE_CLOUD_PROJECT="$PROJECT_ID" bash scripts/gcp/reconcile-fhir-stores.sh
  GOOGLE_CLOUD_PROJECT="$PROJECT_ID" bash scripts/gcp/bootstrap.sh
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
      --header "Authorization: Bearer ${token}" \
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
  all)
    phase_preflight
    phase_deps
    phase_init
    phase_apis
    phase_images
    phase_apply
    bash scripts/gcp/record-readers.sh
    phase_bootstrap
    phase_smoke
    ;;
  *)
    echo "Unknown deploy phase: ${PHASE}" >&2
    exit 1
    ;;
esac
