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
REPOSITORY="${REGION}-docker.pkg.dev/${PROJECT_ID}/ema-flow"

tf_common_vars=(
  -var="project_id=${PROJECT_ID}"
  -var="region=${REGION}"
  -var="environment=${ENVIRONMENT}"
)

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
  if [[ -f package-lock.json ]]; then
    npm ci --no-audit --no-fund
  else
    echo "package-lock.json is missing; falling back to npm install." >&2
    npm install --no-audit --no-fund
  fi
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
  if ! terraform -chdir=infra state show google_artifact_registry_repository.images >/dev/null 2>&1; then
    if gcloud --quiet artifacts repositories describe ema-flow --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
      echo "Importing pre-existing Artifact Registry repository into Terraform state."
      terraform -chdir=infra import \
        "${tf_common_vars[@]}" \
        -var="worker_image=us-docker.pkg.dev/cloudrun/container/hello" \
        -var="validator_image=us-docker.pkg.dev/cloudrun/container/hello" \
        -var="query_image=us-docker.pkg.dev/cloudrun/container/hello" \
        google_artifact_registry_repository.images \
        "projects/${PROJECT_ID}/locations/${REGION}/repositories/ema-flow"
    fi
  fi

  terraform -chdir=infra apply \
    -input=false \
    -auto-approve \
    -target=google_project_service.required \
    -target=google_artifact_registry_repository.images \
    -target=google_project_iam_member.cloudbuild_default_compute_builder \
    -target=google_logging_project_bucket_config.regulated_audit \
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
  # The IAM grant for Cloud Build's default runtime service account (phase_apis)
  # can take several minutes to propagate on a cold-started project, so retry a
  # transient permission-denied here rather than failing the whole deploy on it.
  local max_attempts=8
  local attempt
  for attempt in $(seq 1 "$max_attempts"); do
    if gcloud --quiet builds submit \
      --project="$PROJECT_ID" \
      --config=cloudbuild.images.yaml \
      --substitutions="_REGION=${REGION},_IMAGE_TAG=${TAG}" \
      .; then
      return 0
    fi
    if [[ "$attempt" -lt "$max_attempts" ]]; then
      echo "Cloud Build submit failed (attempt ${attempt}/${max_attempts}); retrying in 30s in case the IAM grant is still propagating." >&2
      sleep 30
    fi
  done
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
    "https://${REGION}-docker.pkg.dev/v2/${PROJECT_ID}/ema-flow/${image_name}/manifests/${tag}" \
    | tr -d '\r' | grep -i '^docker-content-digest:' | awk '{print $2}')"
  if [[ -z "$digest" ]]; then
    echo "Could not resolve a digest for ${image_name}:${tag} from the registry manifest response." >&2
    exit 1
  fi
  printf '%s' "$digest"
}

phase_apply() {
  echo "=== terraform apply ==="
  WORKER_DIGEST="$(resolve_image_digest worker "$TAG")"
  VALIDATOR_DIGEST="$(resolve_image_digest validator "$TAG")"
  QUERY_DIGEST="$(resolve_image_digest query "$TAG")"
  if ! terraform -chdir=infra apply \
    -input=false \
    -auto-approve \
    "${tf_common_vars[@]}" \
    -var="worker_image=${REPOSITORY}/worker@${WORKER_DIGEST}" \
    -var="validator_image=${REPOSITORY}/validator@${VALIDATOR_DIGEST}" \
    -var="query_image=${REPOSITORY}/query@${QUERY_DIGEST}"; then
    echo "=== terraform apply failed; dumping recent container logs for diagnosis ===" >&2
    # Only the worker's structured logs, whose fields are sanitised by src/lib/logger.ts; the
    # validator sidecar's free-text console output is never copied into deploy logs.
    gcloud --quiet logging read \
      "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"ema-flow-${ENVIRONMENT}-worker\" AND resource.labels.container_name=\"worker\"" \
      --project="$PROJECT_ID" \
      --order=asc \
      --freshness=1h \
      --limit=500 \
      --format="value(timestamp,severity,jsonPayload.stage,jsonPayload.message,jsonPayload.errorCount)" || true
    return 1
  fi
}

phase_bootstrap() {
  echo "=== reconcile FHIR stores and import profiles ==="
  GOOGLE_CLOUD_PROJECT="$PROJECT_ID" bash scripts/gcp/reconcile-fhir-stores.sh
  GOOGLE_CLOUD_PROJECT="$PROJECT_ID" bash scripts/gcp/bootstrap.sh
  echo "Deployment complete."
  terraform -chdir=infra output workflow_console_url
  terraform -chdir=infra output bigquery_console_url
}

case "$PHASE" in
  preflight) phase_preflight ;;
  deps) phase_deps ;;
  init) phase_init ;;
  apis) phase_apis ;;
  images) phase_images ;;
  apply) phase_apply ;;
  bootstrap) phase_bootstrap ;;
  all)
    phase_preflight
    phase_deps
    phase_init
    phase_apis
    phase_images
    phase_apply
    phase_bootstrap
    ;;
  *)
    echo "Unknown deploy phase: ${PHASE}" >&2
    exit 1
    ;;
esac
