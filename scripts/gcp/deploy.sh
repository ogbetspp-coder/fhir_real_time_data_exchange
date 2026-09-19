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
  terraform -chdir=infra init -input=false
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
  terraform -chdir=infra apply \
    -input=false \
    -auto-approve \
    -target=google_project_service.required \
    -target=google_artifact_registry_repository.images \
    "${tf_common_vars[@]}" \
    -var="worker_image=us-docker.pkg.dev/cloudrun/container/hello" \
    -var="validator_image=us-docker.pkg.dev/cloudrun/container/hello"
}

phase_images() {
  echo "=== cloud build images ==="
  gcloud --quiet builds submit \
    --project="$PROJECT_ID" \
    --config=cloudbuild.images.yaml \
    --substitutions="_REGION=${REGION},_IMAGE_TAG=${TAG}" \
    .
}

phase_apply() {
  echo "=== terraform apply ==="
  WORKER_TAG="${REPOSITORY}/worker:${TAG}"
  VALIDATOR_TAG="${REPOSITORY}/validator:${TAG}"
  WORKER_DIGEST="$(gcloud --quiet artifacts docker images describe "$WORKER_TAG" --format='value(image_summary.digest)')"
  VALIDATOR_DIGEST="$(gcloud --quiet artifacts docker images describe "$VALIDATOR_TAG" --format='value(image_summary.digest)')"
  terraform -chdir=infra apply \
    -input=false \
    -auto-approve \
    "${tf_common_vars[@]}" \
    -var="worker_image=${REPOSITORY}/worker@${WORKER_DIGEST}" \
    -var="validator_image=${REPOSITORY}/validator@${VALIDATOR_DIGEST}"
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
