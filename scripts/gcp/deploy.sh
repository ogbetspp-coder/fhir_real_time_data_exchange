#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/gcp/common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
cd "$ROOT"

PROJECT_ID="$(ema_flow_resolve_project)"
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export GCP_PROJECT_ID="${GCP_PROJECT_ID:-$PROJECT_ID}"
export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"
REGION="${GCP_REGION:-europe-west4}"
ENVIRONMENT="${EMA_FLOW_ENVIRONMENT:-dev}"

TAG="$(git rev-parse --short=12 HEAD)"
REPOSITORY="${REGION}-docker.pkg.dev/${PROJECT_ID}/ema-flow"

if [[ -f package-lock.json ]]; then
  npm ci --no-audit --no-fund
else
  echo "package-lock.json is missing; falling back to npm install." >&2
  npm install --no-audit --no-fund
fi

terraform -chdir=infra init -input=false
terraform -chdir=infra apply \
  -input=false \
  -auto-approve \
  -target=google_project_service.required \
  -target=google_artifact_registry_repository.images \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}" \
  -var="environment=${ENVIRONMENT}" \
  -var="worker_image=us-docker.pkg.dev/cloudrun/container/hello" \
  -var="validator_image=us-docker.pkg.dev/cloudrun/container/hello"

gcloud --quiet builds submit \
  --project="$PROJECT_ID" \
  --config=cloudbuild.yaml \
  --substitutions="_REGION=${REGION},_IMAGE_TAG=${TAG}" \
  .

WORKER_TAG="${REPOSITORY}/worker:${TAG}"
VALIDATOR_TAG="${REPOSITORY}/validator:${TAG}"
WORKER_DIGEST="$(gcloud --quiet artifacts docker images describe "$WORKER_TAG" --format='value(image_summary.digest)')"
VALIDATOR_DIGEST="$(gcloud --quiet artifacts docker images describe "$VALIDATOR_TAG" --format='value(image_summary.digest)')"

terraform -chdir=infra apply \
  -input=false \
  -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}" \
  -var="environment=${ENVIRONMENT}" \
  -var="worker_image=${REPOSITORY}/worker@${WORKER_DIGEST}" \
  -var="validator_image=${REPOSITORY}/validator@${VALIDATOR_DIGEST}"

GOOGLE_CLOUD_PROJECT="$PROJECT_ID" scripts/gcp/reconcile-fhir-stores.sh
GOOGLE_CLOUD_PROJECT="$PROJECT_ID" scripts/gcp/bootstrap.sh

echo "Deployment complete."
terraform -chdir=infra output workflow_console_url
terraform -chdir=infra output bigquery_console_url
